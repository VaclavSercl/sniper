// 📐 Grid Brain — SQLite Analytics
// Sniper Armada · Bot #3

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;
use memmap2::MmapMut;
use rusqlite::{Connection, params};
use anyhow::Result;
use chrono::Utc;
use sniper_types::grid_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

fn init_mmap_read<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn init_db(conn: &Connection) -> Result<()> {
    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS grid_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            mid_price REAL, pnl REAL, daily_pnl REAL, fills INTEGER,
            buy_levels INTEGER, sell_levels INTEGER, spacing REAL, paused INTEGER
        );
        CREATE TABLE IF NOT EXISTS grid_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            side TEXT, level INTEGER, price REAL, quantity REAL
        );
        CREATE INDEX IF NOT EXISTS idx_grid_snap_ts ON grid_snapshots(ts);
    ")?;
    Ok(())
}

fn main() -> Result<()> {
    println!("🧠 Grid Brain — SQLite Analytics Engine");
    let e_mmap = init_mmap_read::<GridEngineState>(GRID_ENGINE_PATH)?;
    let r_mmap = init_mmap_read::<GridRiskState>(GRID_RISK_PATH)?;
    let engine = unsafe { &*(e_mmap.as_ptr() as *const GridEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const GridRiskState) };

    let home = std::env::var("HOME").unwrap_or("/home/wwwenda".into());
    let db_dir = format!("{}/.local/share/sniper", home);
    std::fs::create_dir_all(&db_dir)?;
    let conn = Connection::open(format!("{}/grid.db", db_dir))?;
    init_db(&conn)?;

    loop {
        let ts = Utc::now().to_rfc3339();
        let mid = engine.mid_price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let daily = engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let fills = engine.total_fills.load(Ordering::Acquire) as i64;
        let n_buy = engine.active_buy_levels.load(Ordering::Acquire) as i64;
        let n_sell = engine.active_sell_levels.load(Ordering::Acquire) as i64;
        let spacing = risk.grid_spacing.load(Ordering::Acquire) as f64 / PRICE_SCALE;
        let paused = risk.global_paused.load(Ordering::Acquire) as i64;

        let _ = conn.execute(
            "INSERT INTO grid_snapshots (ts,mid_price,pnl,daily_pnl,fills,buy_levels,sell_levels,spacing,paused) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![ts, mid, pnl, daily, fills, n_buy, n_sell, spacing, paused],
        );
        let _ = conn.execute("DELETE FROM grid_snapshots WHERE ts < datetime('now', '-7 days')", []);
        std::thread::sleep(Duration::from_secs(30));
    }
}
