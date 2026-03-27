/// Shared logging/tracing setup for all Sniper bots.
/// Centralizes tracing subscriber configuration so Hydra, Moonshot, Grid, etc.
/// all use the same format, rotation, and filtering.

use tracing_subscriber::{EnvFilter, fmt};

/// Initialize the standard Sniper tracing subscriber.
/// - JSON format for machine parsing
/// - ENV_FILTER from RUST_LOG (default: info)
/// - File appender with daily rotation
pub fn init_tracing(bot_name: &str, log_dir: &str) {
    let file_appender = tracing_appender::rolling::daily(log_dir, format!("{bot_name}.log"));
    let (non_blocking, _guard) = tracing_appender::non_blocking(file_appender);

    // Leak the guard so it lives for the entire program
    // (tracing_appender requires the guard to stay alive)
    std::mem::forget(_guard);

    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info"));

    fmt()
        .with_env_filter(filter)
        .with_target(true)
        .with_thread_ids(false)
        .with_writer(non_blocking)
        .json()
        .init();
}

/// Initialize tracing for development (stdout, human-readable)
pub fn init_tracing_dev() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info"));

    fmt()
        .with_env_filter(filter)
        .with_target(true)
        .init();
}
