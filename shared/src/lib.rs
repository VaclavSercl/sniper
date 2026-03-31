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

// ═══ Runtime utilities (behind 'runtime' feature) ═══
// Enable with: sniper-shared = { path = "../shared", features = ["runtime"] }
#[cfg(feature = "runtime")]
pub mod notifier;
#[cfg(feature = "runtime")]
pub mod mmap_utils;
#[cfg(feature = "runtime")]
pub mod lock;

pub use types::*;
