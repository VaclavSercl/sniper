// ═══════════════════════════════════════════════════════════
// 🧠 CandleL1Brain — In-Process GGUF Inference Engine
// v21.0 Pure Rust Hive
//
// Pure Rust in-process GGUF inference via Candle.
// Phi-3.5 Q4_K_M (2.39GB) in-process on GTX 1060.
// ═══════════════════════════════════════════════════════════

use anyhow::{Context, Result};
use candle_core::{Device, Tensor};
use candle_transformers::models::quantized_llama::ModelWeights;
use hf_hub::{api::sync::Api, Repo, RepoType};
use tokenizers::Tokenizer;
use tracing::info;

use crate::logit_sniper::{HftAction, LogitSniper};

pub const DEFAULT_MODEL_REPO: &str = "bartowski/Llama-3.2-1B-Instruct-GGUF";
pub const DEFAULT_MODEL_FILE: &str = "Llama-3.2-1B-Instruct-Q4_K_M.gguf";
pub const DEFAULT_TOKENIZER_REPO: &str = "unsloth/Llama-3.2-1B-Instruct";

pub struct CandleL1Brain {
    model: ModelWeights,
    tokenizer: Tokenizer,
    sniper: LogitSniper,
    device: Device,
    token_pos: usize,
}

pub struct BrainConfig {
    pub model_repo: String,
    pub model_file: String,
    pub tokenizer_repo: String,
    pub cuda_device: usize,
}

impl Default for BrainConfig {
    fn default() -> Self {
        Self {
            model_repo: DEFAULT_MODEL_REPO.to_string(),
            model_file: DEFAULT_MODEL_FILE.to_string(),
            tokenizer_repo: DEFAULT_TOKENIZER_REPO.to_string(),
            cuda_device: 0,
        }
    }
}

impl CandleL1Brain {
    /// Boot — called ONCE at startup (~10-30s, cached after first run).
    pub fn boot(config: &BrainConfig) -> Result<Self> {
        info!("Candle L1 Brain booting: {}/{}", config.model_repo, config.model_file);

        let device = if cfg!(feature = "cuda") {
            Device::new_cuda(config.cuda_device)
                .context("CUDA init failed")?
        } else {
            info!("  CPU mode (GTX 1060 sm_61 — no fp16 atomics)");
            Device::Cpu
        };
        info!("  Device ready");

        let api = Api::new().context("HF Hub init failed")?;

        // Download GGUF weights
        let model_repo = api.repo(Repo::with_revision(
            config.model_repo.clone(), RepoType::Model, "main".into(),
        ));
        let weights_path = model_repo.get(&config.model_file)
            .context("GGUF download failed")?;
        info!("  Weights: {}", weights_path.display());

        // Download tokenizer
        let tok_repo = api.repo(Repo::with_revision(
            config.tokenizer_repo.clone(), RepoType::Model, "main".into(),
        ));
        let tok_path = tok_repo.get("tokenizer.json")
            .context("tokenizer.json download failed")?;

        let tokenizer = Tokenizer::from_file(&tok_path)
            .map_err(|e| anyhow::anyhow!("Tokenizer: {}", e))?;
        info!("  Tokenizer vocab: {}", tokenizer.get_vocab_size(true));

        // Load GGUF into VRAM
        info!("  Loading into VRAM...");
        let mut file = std::fs::File::open(&weights_path)?;
        let content = candle_core::quantized::gguf_file::Content::read(&mut file)?;
        let model = ModelWeights::from_gguf(content, &mut file, &device)?;
        info!("  Model in VRAM");

        let sniper = LogitSniper::from_tokenizer(&tokenizer)
            .context("Token resolution failed")?;

        info!("Candle L1 Brain ONLINE");
        Ok(Self { model, tokenizer, sniper, device, token_pos: 0 })
    }

    pub fn boot_default() -> Result<Self> {
        Self::boot(&BrainConfig::default())
    }

    /// Build Few-Shot anchored prompt for Logit Sniping.
    ///
    /// Design: Explicit RULES + 3 in-context examples force the model
    /// into a "decision tension" state. The prompt ends with "ACTION: "
    /// and we snipe the logits at that exact position.
    ///
    /// TOX = toxicity score [0.0, 1.0] from toxic flow detector.
    fn build_prompt(obi: f64, spread_bps: f64, _delta: f64, tox: f64) -> String {
        format!(
            "ROLE: Ultra-low latency HFT Reflex Core.\n\
             TASK: Classify L2 microstructure to predict next 100ms price tick.\n\
             OUTPUT: EXACTLY ONE TOKEN FROM [BID, ASK, HOLD, KILL].\n\
             RULES:\n\
             1. TOX > 0.70 -> KILL (Toxic sweep imminent, cancel all limits).\n\
             2. OBI > 0.40 AND TOX < 0.40 -> BID (Buy pressure, skew up).\n\
             3. OBI < -0.40 AND TOX < 0.40 -> ASK (Sell pressure, skew down).\n\
             4. Otherwise -> HOLD.\n\
             \n\
             DATA: OBI:0.85 | SPR:0.5 | TOX:0.10 -> ACTION: BID\n\
             DATA: OBI:-0.60 | SPR:0.5 | TOX:0.15 -> ACTION: ASK\n\
             DATA: OBI:0.10 | SPR:2.5 | TOX:0.85 -> ACTION: KILL\n\
             DATA: OBI:{:.2} | SPR:{:.1} | TOX:{:.2} -> ACTION: ",
            obi, spread_bps, tox
        )
    }

    /// HFT Reflex — HOT PATH (<3ms target).
    ///
    /// 1 forward pass → Logit Snipe → HftAction
    pub fn reflex_action(
        &mut self,
        obi: f64,
        spread_bps: f64,
        delta_lead: f64,
        volatility: f64,
    ) -> Result<(HftAction, f32)> {
        // 1. Build prompt
        let prompt = Self::build_prompt(obi, spread_bps, delta_lead, volatility);

        // 2. Tokenize (in-process, ~10µs)
        let encoding = self.tokenizer.encode(prompt.as_str(), true)
            .map_err(|e| anyhow::anyhow!("Tokenize: {}", e))?;
        let tokens = encoding.get_ids();

        // 3. Input MUSÍ mít batch dimenzi [1, seq_len] —
        //    quantized_llama interně vyžaduje 2D pro RoPE/Attention
        let input = Tensor::new(tokens, &self.device)?.unsqueeze(0)?;

        // 4. Forward pass (stateless, pos=0)
        let logits = self.model.forward(&input, 0)?;

        // 5. Bezpečná extrakce výstupních logitů:
        //    Pattern matching na tvar dimenzí (slice)
        let logits_vec: Vec<f32> = match logits.dims() {
            // Varianta A (Optimalizováno): Model vrátil jen poslední token [1, vocab_size]
            [1, _vocab_size] => logits.squeeze(0)?.to_vec1()?,

            // Varianta B (Standard): Model vrátil celou sekvenci [1, seq_len, vocab_size]
            [1, seq, _vocab] => logits.squeeze(0)?.get(seq - 1)?.to_vec1()?,

            // Pojistka proti změně API v budoucích verzích Candle
            other => anyhow::bail!("Neznámý tvar logits: {:?}", other),
        };

        // 6. Logit Snipe → HftAction
        let (action, confidence) = self.sniper.snipe(&logits_vec);
        Ok((action, confidence))
    }

    /// Reset KV cache position
    pub fn reset_cache(&mut self) {
        // Jelikož nyní nepoužíváme self.token_pos pro inkrementaci, 
        // tato funkce primárně slouží k vyčištění vnitřního stavu, 
        // pokud bys v budoucnu implementoval explicitní KV Cache struct.
        self.token_pos = 0;
    }

    /// Get the current LogitSniper for inspection.
    pub fn sniper(&self) -> &LogitSniper {
        &self.sniper
    }
}
