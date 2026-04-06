mod types;
pub mod fee_types;
pub mod l2_command;
pub mod math;
pub mod logging;
pub mod moonshot_types;
pub mod grid_types;
pub mod trigon_types;
pub mod pnl_types;
pub mod exchange;
pub mod bot_manifest;
pub mod ml_types;
pub mod ml_shield;
pub mod armada_types;

// ═══ Runtime utilities (behind 'runtime' feature) ═══
// Enable with: sniper-shared = { path = "../shared", features = ["runtime"] }
#[cfg(feature = "runtime")]
pub mod notifier;
#[cfg(feature = "runtime")]
pub mod mmap_utils;
#[cfg(feature = "runtime")]
pub mod lock;
#[cfg(feature = "runtime")]
pub mod framework;

pub use types::*;
