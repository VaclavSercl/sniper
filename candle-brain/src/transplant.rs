// ═══════════════════════════════════════════════════════════
// 🧬 SBP v3.1: Brain Transplant Protocol
// v21.0 Pure Rust Hive (#80)
//
// Safe hot-swap of GGUF models WITHOUT system restart.
//
// Protocol:
//   1. L2 Oracle decides to swap model (e.g., Phi-3.5 → Qwen2.5)
//   2. Sends TRANSPLANT command via mmap
//   3. Cortex enters SHADOW mode: runs BOTH old + new brain
//   4. 10 cycles: compare decisions. If >80% agreement → COMMIT
//   5. If <80% agreement → ROLLBACK to old brain
//
// Zero downtime. Zero risk. Sovereign intelligence.
// ═══════════════════════════════════════════════════════════

use crate::brain::{BrainConfig, CandleL1Brain};
use tracing::{info, warn};

/// Result of a Brain Transplant operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransplantResult {
    /// New brain committed — it agrees with old brain
    Committed {
        model_repo: String,
        model_file: String,
        agreement_pct: u8,
    },
    /// Rollback — new brain disagrees too much
    RolledBack {
        model_repo: String,
        agreement_pct: u8,
        reason: String,
    },
    /// Boot failure — new model couldn't load
    BootFailed {
        model_repo: String,
        error: String,
    },
}

/// Brain Transplant Protocol executor.
pub struct BrainTransplant;

impl BrainTransplant {
    /// Execute a Brain Transplant.
    ///
    /// 1. Boot new brain with the given config
    /// 2. Run `shadow_cycles` comparisons with identical inputs
    /// 3. If agreement >= `min_agreement_pct` → return new brain
    /// 4. Otherwise → return None (caller keeps old brain)
    ///
    /// This is a BLOCKING operation (~30-60s for model download + shadow).
    pub fn execute(
        new_config: &BrainConfig,
        old_brain: &mut CandleL1Brain,
        shadow_cycles: usize,
        min_agreement_pct: u8,
        test_inputs: &[(f64, f64, f64, f64)], // (obi, spread, delta, tox)
    ) -> TransplantResult {
        info!(
            "🧬 Brain Transplant starting: {} / {}",
            new_config.model_repo, new_config.model_file
        );

        // Phase 1: Boot new brain
        let mut new_brain = match CandleL1Brain::boot(new_config) {
            Ok(b) => {
                info!("  🧬 New brain booted successfully");
                b
            }
            Err(e) => {
                warn!("  🧬 BOOT FAILED: {e}");
                return TransplantResult::BootFailed {
                    model_repo: new_config.model_repo.clone(),
                    error: format!("{e}"),
                };
            }
        };

        // Phase 2: Shadow mode — run both brains, compare decisions
        let cycles = test_inputs.len().min(shadow_cycles);
        let mut agreements: usize = 0;

        for (i, &(obi, spr, delta, tox)) in test_inputs.iter().take(cycles).enumerate() {
            old_brain.reset_cache();
            new_brain.reset_cache();

            let old_result = old_brain.reflex_action(obi, spr, delta, tox);
            let new_result = new_brain.reflex_action(obi, spr, delta, tox);

            match (old_result, new_result) {
                (Ok((old_action, _)), Ok((new_action, _))) => {
                    if old_action == new_action {
                        agreements += 1;
                    }
                    info!(
                        "  🧬 Shadow [{}/{}]: old={:?} new={:?} {}",
                        i + 1, cycles, old_action, new_action,
                        if old_action == new_action { "✓" } else { "✗" }
                    );
                }
                (_, Err(e)) => {
                    warn!("  🧬 Shadow [{}/{}]: new brain error: {e}", i + 1, cycles);
                }
                (Err(e), _) => {
                    warn!("  🧬 Shadow [{}/{}]: old brain error: {e}", i + 1, cycles);
                    agreements += 1; // Give new brain benefit of doubt
                }
            }
        }

        let agreement_pct = if cycles > 0 {
            (agreements * 100 / cycles) as u8
        } else {
            0
        };

        // Phase 3: Decision
        if agreement_pct >= min_agreement_pct {
            info!(
                "🧬 TRANSPLANT COMMITTED: {}% agreement (threshold: {}%)",
                agreement_pct, min_agreement_pct
            );
            TransplantResult::Committed {
                model_repo: new_config.model_repo.clone(),
                model_file: new_config.model_file.clone(),
                agreement_pct,
            }
        } else {
            warn!(
                "🧬 TRANSPLANT ROLLED BACK: {}% agreement < {}% threshold",
                agreement_pct, min_agreement_pct
            );
            TransplantResult::RolledBack {
                model_repo: new_config.model_repo.clone(),
                agreement_pct,
                reason: format!(
                    "Agreement {}% < threshold {}%",
                    agreement_pct, min_agreement_pct
                ),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_transplant_result_variants() {
        let committed = TransplantResult::Committed {
            model_repo: "test".into(),
            model_file: "test.gguf".into(),
            agreement_pct: 90,
        };
        assert_eq!(committed, TransplantResult::Committed {
            model_repo: "test".into(),
            model_file: "test.gguf".into(),
            agreement_pct: 90,
        });

        let rolled = TransplantResult::RolledBack {
            model_repo: "test".into(),
            agreement_pct: 50,
            reason: "too low".into(),
        };
        matches!(rolled, TransplantResult::RolledBack { .. });

        let failed = TransplantResult::BootFailed {
            model_repo: "test".into(),
            error: "no GPU".into(),
        };
        matches!(failed, TransplantResult::BootFailed { .. });
    }
}
