// ═══════════════════════════════════════════════════════════
// 🔒 Single-Instance Lock — Prevents duplicate bot processes
// Part of sniper-shared crate (runtime feature)
//
// Eliminates 4× duplicate lock patterns across bots.
// Uses fs2 advisory file locking on /tmp/{name}.lock
// ═══════════════════════════════════════════════════════════

use anyhow::{Context, Result, bail};
use fs2::FileExt;

/// Ensure only one instance of the given bot is running.
///
/// Creates `/tmp/{bot_name}.lock` and acquires an exclusive advisory lock.
/// If another instance holds the lock, aborts with an error.
///
/// Returns the lock file handle — **must be kept alive** for the duration
/// of the process. Dropping it releases the lock.
///
/// # Example
/// ```no_run
/// let _lock = sniper_types::lock::ensure_single_instance("moonshot-core")?;
/// // Lock held until _lock is dropped (end of main)
/// ```
pub fn ensure_single_instance(bot_name: &str) -> Result<std::fs::File> {
    let lock_path = format!("/tmp/{}.lock", bot_name);
    let lock_file = std::fs::File::create(&lock_path)
        .with_context(|| format!("Failed to create lock file: {}", lock_path))?;

    if lock_file.try_lock_exclusive().is_err() {
        tracing::error!(
            event = "dual_instance_blocked",
            bot = bot_name,
            msg = "Another instance is already running! Aborting."
        );
        bail!("Another {} is already running (lock: {})", bot_name, lock_path);
    }

    Ok(lock_file)
}
