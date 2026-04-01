// ═══════════════════════════════════════════════════════════
// 🧠 candle-brain — In-Process L1 AI for Sniper Armada HFT
// v21.0 Pure Rust Hive
//
// Replaces LM Studio HTTP inference with zero-copy Candle.
// Phi-3.5 Q4_K_M (2.39GB) runs in-process on GTX 1060.
//
// Key innovation: Logit Sniping
// - NO text generation (too slow for HFT)
// - 1 forward pass → read 3 logits (HOLD/BID/ASK)
// - <3ms per decision vs 15-50ms HTTP
// ═══════════════════════════════════════════════════════════

pub mod brain;
pub mod logit_sniper;

pub use brain::CandleL1Brain;
pub use logit_sniper::{HftAction, LogitSniper};
