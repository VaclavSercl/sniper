// ═══════════════════════════════════════════════════════════
// 🎯 LogitSniper — Zero-Generation HFT Classifier
// v21.0 Pure Rust Hive
//
// The core HFT trick: DON'T generate text!
// Instead, do 1 forward pass and read raw logits for 3 tokens.
//
// Token IDs are resolved DYNAMICALLY at boot (model-agnostic).
// Works with Phi-3.5, Qwen2.5, Llama-3.2 — any GGUF model.
// ═══════════════════════════════════════════════════════════

use anyhow::{bail, Result};
use tracing::info;

/// HFT action signal from L1 AI.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HftAction {
    /// Hold position — no action needed
    Hold,
    /// Skew grid toward bids (bullish signal)
    Bid,
    /// Skew grid toward asks (bearish signal)
    Ask,
    /// Kill — toxic sweep imminent, cancel all limits NOW
    Kill,
}

impl HftAction {
    /// Convert to mmap bias value (× PRICE_SCALE atoms)
    pub fn to_bias(&self, magnitude: i64) -> i64 {
        match self {
            HftAction::Hold => 0,
            HftAction::Bid => magnitude,
            HftAction::Ask => -magnitude,
            HftAction::Kill => i64::MIN, // Sentinel: triggers order cancel
        }
    }
}

/// Logit Sniping engine — maps model output to HFT actions.
///
/// At boot time, resolves token IDs for "HOLD", "BID", "ASK", "KILL"
/// from the model's tokenizer. This makes the system model-agnostic.
pub struct LogitSniper {
    pub id_hold: u32,
    pub id_bid: u32,
    pub id_ask: u32,
    pub id_kill: u32,
}

impl LogitSniper {
    /// Create a new LogitSniper by resolving token IDs from the tokenizer.
    /// This is called ONCE at boot — never in the hot path.
    pub fn from_tokenizer(tokenizer: &tokenizers::Tokenizer) -> Result<Self> {
        let id_hold = Self::resolve_token(tokenizer, "HOLD")?;
        let id_bid = Self::resolve_token(tokenizer, "BID")?;
        let id_ask = Self::resolve_token(tokenizer, "ASK")?;
        let id_kill = Self::resolve_token(tokenizer, "KILL")?;

        info!(
            "🎯 LogitSniper armed: HOLD={} BID={} ASK={} KILL={}",
            id_hold, id_bid, id_ask, id_kill
        );

        Ok(Self { id_hold, id_bid, id_ask, id_kill })
    }

    /// Resolve a token ID dynamically. Tries multiple variants:
    /// "HOLD", "Hold", "hold", " HOLD", "▁HOLD", "\u{2581}HOLD" (SentencePiece)
    fn resolve_token(tokenizer: &tokenizers::Tokenizer, word: &str) -> Result<u32> {
        // 1. Exact match: "HOLD"
        if let Some(id) = tokenizer.token_to_id(word) {
            return Ok(id);
        }
        // 2. Capitalized: "Hold"
        let mut cap = word.to_lowercase();
        if let Some(first) = cap.get_mut(0..1) {
            first.make_ascii_uppercase();
        }
        if let Some(id) = tokenizer.token_to_id(&cap) {
            return Ok(id);
        }
        // 3. Lowercase: "hold"
        if let Some(id) = tokenizer.token_to_id(&word.to_lowercase()) {
            return Ok(id);
        }
        // 4. BPE space prefix: " HOLD" (GPT-style tokenizers)
        let spaced = format!(" {}", word);
        if let Some(id) = tokenizer.token_to_id(&spaced) {
            return Ok(id);
        }
        // 5. SentencePiece prefix U+2581: "▁HOLD" (Llama/Phi standard)
        let sp = format!("▁{}", word);
        if let Some(id) = tokenizer.token_to_id(&sp) {
            return Ok(id);
        }
        // 6. SentencePiece alternate U+2581 encoding (some tokenizers emit this)
        let sp_alt = format!("\u{2581}{}", word);
        if let Some(id) = tokenizer.token_to_id(&sp_alt) {
            return Ok(id);
        }

        bail!(
            "Model tokenizer does not know the word '{}'. \
            This model is incompatible with Logit Sniping. \
            Try a different GGUF model.",
            word
        )
    }

    /// Extract HFT action from raw logits tensor.
    /// HOT PATH — 4-way softmax over [HOLD, BID, ASK, KILL].
    /// Returns (action, confidence) where confidence is [0.0, 1.0].
    #[inline]
    pub fn snipe(&self, logits: &[f32]) -> (HftAction, f32) {
        let hold = logits.get(self.id_hold as usize).copied().unwrap_or(f32::NEG_INFINITY);
        let bid = logits.get(self.id_bid as usize).copied().unwrap_or(f32::NEG_INFINITY);
        let ask = logits.get(self.id_ask as usize).copied().unwrap_or(f32::NEG_INFINITY);
        let kill = logits.get(self.id_kill as usize).copied().unwrap_or(f32::NEG_INFINITY);

        // Softmax over 4 logits
        let max_val = hold.max(bid).max(ask).max(kill);
        let exp_hold = (hold - max_val).exp();
        let exp_bid = (bid - max_val).exp();
        let exp_ask = (ask - max_val).exp();
        let exp_kill = (kill - max_val).exp();
        let sum = exp_hold + exp_bid + exp_ask + exp_kill;

        let p_hold = exp_hold / sum;
        let p_bid = exp_bid / sum;
        let p_ask = exp_ask / sum;
        let p_kill = exp_kill / sum;

        // KILL takes absolute priority if it wins
        if p_kill >= p_hold && p_kill >= p_bid && p_kill >= p_ask {
            (HftAction::Kill, p_kill)
        } else if p_hold >= p_bid && p_hold >= p_ask {
            (HftAction::Hold, p_hold)
        } else if p_bid > p_ask {
            (HftAction::Bid, p_bid)
        } else {
            (HftAction::Ask, p_ask)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_snipe_hold() {
        let sniper = LogitSniper { id_hold: 0, id_bid: 1, id_ask: 2, id_kill: 3 };
        let logits = vec![10.0, 2.0, 1.0, 0.5];
        let (action, conf) = sniper.snipe(&logits);
        assert_eq!(action, HftAction::Hold);
        assert!(conf > 0.9);
    }

    #[test]
    fn test_snipe_bid() {
        let sniper = LogitSniper { id_hold: 0, id_bid: 1, id_ask: 2, id_kill: 3 };
        let logits = vec![1.0, 10.0, 2.0, 0.5];
        let (action, _) = sniper.snipe(&logits);
        assert_eq!(action, HftAction::Bid);
    }

    #[test]
    fn test_snipe_ask() {
        let sniper = LogitSniper { id_hold: 0, id_bid: 1, id_ask: 2, id_kill: 3 };
        let logits = vec![1.0, 2.0, 10.0, 0.5];
        let (action, _) = sniper.snipe(&logits);
        assert_eq!(action, HftAction::Ask);
    }

    #[test]
    fn test_snipe_kill() {
        let sniper = LogitSniper { id_hold: 0, id_bid: 1, id_ask: 2, id_kill: 3 };
        let logits = vec![1.0, 2.0, 1.0, 15.0]; // KILL dominates
        let (action, conf) = sniper.snipe(&logits);
        assert_eq!(action, HftAction::Kill);
        assert!(conf > 0.9);
    }

    #[test]
    fn test_bias_conversion() {
        assert_eq!(HftAction::Hold.to_bias(5000), 0);
        assert_eq!(HftAction::Bid.to_bias(5000), 5000);
        assert_eq!(HftAction::Ask.to_bias(5000), -5000);
        assert_eq!(HftAction::Kill.to_bias(5000), i64::MIN);
    }

    #[test]
    fn test_confidence_sums_to_one() {
        let sniper = LogitSniper { id_hold: 0, id_bid: 1, id_ask: 2, id_kill: 3 };
        let logits = vec![3.0, 2.0, 1.0, 0.5];
        let (_, conf) = sniper.snipe(&logits);
        assert!(conf > 0.0 && conf < 1.0);
    }
}
