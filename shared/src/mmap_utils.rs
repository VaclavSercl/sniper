// ═══════════════════════════════════════════════════════════
// 🗺️ mmap Utilities — Unified memory-mapped file initialization
// Part of sniper-shared crate (runtime feature)
//
// Eliminates 3× duplicate `init_mmap_ptr<T>` across bots.
// Provides safe, generic mmap initialization with default values.
// ═══════════════════════════════════════════════════════════

use std::fs::OpenOptions;
use std::path::Path;

use anyhow::{Context, Result};
use memmap2::MmapMut;

/// Initialize a memory-mapped file for type T with default values.
///
/// - Creates parent directories if needed
/// - Sets file size to `size_of::<T>()`
/// - If file is all-zeros (fresh), writes `T::default()` into it
/// - Returns the raw `MmapMut` — caller casts to `*const T`
///
/// # Safety
/// The caller must ensure:
/// - `T` is `#[repr(C)]` with deterministic layout
/// - The mmap pointer is not aliased mutably by multiple writers
///
/// # Example
/// ```no_run
/// use sniper_types::mmap_utils::init_mmap;
/// use sniper_types::moonshot_types::{MoonshotEngineState, MOONSHOT_ENGINE_PATH};
///
/// let mmap = init_mmap::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH)?;
/// let engine = unsafe { &*(mmap.as_ptr() as *const MoonshotEngineState) };
/// ```
pub fn init_mmap<T: Default>(path: &str) -> Result<MmapMut> {
    let dir = Path::new(path)
        .parent()
        .unwrap_or_else(|| Path::new("/dev/shm/sniper"));
    std::fs::create_dir_all(dir)
        .with_context(|| format!("Failed to create mmap dir: {}", dir.display()))?;

    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .open(path)
        .with_context(|| format!("Failed to open mmap file: {}", path))?;

    file.set_len(std::mem::size_of::<T>() as u64)?;
    let mut mmap = unsafe { MmapMut::map_mut(&file)? };

    // Initialize with T::default() if file is fresh (all zeros)
    if mmap.iter().all(|&b| b == 0) {
        let default_val = T::default();
        let ptr = &default_val as *const T as *const u8;
        let slice = unsafe { std::slice::from_raw_parts(ptr, std::mem::size_of::<T>()) };
        mmap.copy_from_slice(slice);
    }

    Ok(mmap)
}

/// Open an existing mmap file read-only (for consumers like Nexus).
///
/// Does NOT create the file — returns error if it doesn't exist.
/// Despite returning `MmapMut`, the intent is read-only access.
/// Use `Mmap` (read-only) variant when truly read-only access suffices.
pub fn open_mmap_readonly(path: &str) -> Result<memmap2::Mmap> {
    let file = OpenOptions::new()
        .read(true)
        .open(path)
        .with_context(|| format!("Cannot open mmap (read-only): {}", path))?;
    let mmap = unsafe { memmap2::Mmap::map(&file)? };
    Ok(mmap)
}

/// Open an existing mmap file read-write (for bots that both read and write).
pub fn open_mmap_readwrite(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .with_context(|| format!("Cannot open mmap (read-write): {}", path))?;
    let mmap = unsafe { MmapMut::map_mut(&file)? };
    Ok(mmap)
}
