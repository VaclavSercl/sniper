// ═══════════════════════════════════════════════════════════
// 🧠 candle-brain — In-Process L1 AI for Sniper Armada HFT
// v21.0 Pure Rust Hive
//
// Pure Rust in-process GGUF inference via Candle.
// Phi-3.5 Q4_K_M (2.39GB) runs in-process on GTX 1060.
//
// Key innovation: Logit Sniping
// - NO text generation (too slow for HFT)
// - 1 forward pass → read 4 logits (HOLD/BID/ASK/KILL)
// - <20ms per decision vs 50-100ms HTTP
//
// Model Agnosticism: chat_template auto-detects model family
// and wraps prompts in the correct template format.
// ═══════════════════════════════════════════════════════════

pub mod brain;
pub mod chat_template;
pub mod logit_sniper;
pub mod transplant;

pub use brain::{BrainConfig, CandleL1Brain};
pub use chat_template::ModelFamily;
pub use logit_sniper::{HftAction, LogitSniper};
pub use transplant::{BrainTransplant, TransplantResult};

