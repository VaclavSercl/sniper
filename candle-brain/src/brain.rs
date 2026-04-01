// ═══════════════════════════════════════════════════════════
// 🧠 CandleL1Brain — In-Process GGUF Inference Engine
// v21.0 Pure Rust Hive
//
// Replaces LM Studio HTTP with zero-copy Candle.
// Phi-3.5 Q4_K_M (2.39GB) in-process on GTX 1060.
// ═══════════════════════════════════════════════════════════

use anyhow::{Context, Result};
use candle_core::{Device, Tensor};
use candle_transformers::models::quantized_llama::ModelWeights;
use hf_hub::{api::sync::Api, Repo, RepoType};
use tokenizers::Tokenizer;
use tracing::info;

use crate::logit_sniper::{HftAction, LogitSniper};

pub const DEFAULT_MODEL_REPO: &str = "microsoft/Phi-3.5-mini-instruct-GGUF";
pub const DEFAULT_MODEL_FILE: &str = "Phi-3.5-mini-instruct-q4_k_m.gguf";
pub const DEFAULT_TOKENIZER_REPO: &str = "microsoft/Phi-3.5-mini-instruct";

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
    fn build_prompt(obi: f64, spread_bps: f64, delta: f64, tox: f64) -> String {
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
        let n_tokens = tokens.len();

        // 3. Create input tensor on CUDA
        let input = Tensor::new(tokens, &self.device)?.unsqueeze(0)?;

        // 4. Forward pass (1-3ms on GTX 1060 with Q4_K_M)
        let logits = self.model.forward(&input, self.token_pos)?;
        self.token_pos += n_tokens;

        // 5. Extract last token logits
        let last_logits = logits.squeeze(0)?;
        let seq_len = last_logits.dim(0)?;
        let last_token_logits = last_logits.get(seq_len - 1)?;
        let logits_vec: Vec<f32> = last_token_logits.to_vec1()?;

        // 6. Logit Snipe → HftAction
        let (action, confidence) = self.sniper.snipe(&logits_vec);
        Ok((action, confidence))
    }

    /// Reset KV cache position (call periodically to prevent OOM).
    pub fn reset_cache(&mut self) {
        self.token_pos = 0;
        // Note: ModelWeights doesn't expose clear_kv_cache() in all versions
        // This is a soft reset — model will re-warm on next forward pass
    }

    /// Get the current LogitSniper for inspection.
    pub fn sniper(&self) -> &LogitSniper {
        &self.sniper
    }
}
