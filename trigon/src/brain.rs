// 🔺 Trigon Brain — SQLite Analytics
// Sniper Armada · Bot #4

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;
use memmap2::MmapMut;
use rusqlite::{Connection, params};
use anyhow::Result;
use chrono::Utc;
use sniper_types::trigon_types::*;
use sniper_types::moonshot_types::symbol_hash_to_str;
use sniper_types::PRICE_SCALE_I;

fn init_mmap_read<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn init_db(conn: &Connection) -> Result<()> {
    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS trigon_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            total_arbs INTEGER, total_pnl REAL, daily_pnl REAL,
            best_profit_bps REAL, scan_latency_ns INTEGER, paused INTEGER
        );
        CREATE TABLE IF NOT EXISTS trigon_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            triangle_idx INTEGER, implied_rate REAL, profit_bps REAL,
            leg0 TEXT, leg1 TEXT, leg2 TEXT, executed INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_trigon_snap_ts ON trigon_snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_trigon_opp_ts ON trigon_opportunities(ts);
    ")?;
    Ok(())
}

fn main() -> Result<()> {
    println!("🧠 Trigon Brain — SQLite Analytics Engine");
    let e_mmap = init_mmap_read::<TrigonEngineState>(TRIGON_ENGINE_PATH)?;
    let r_mmap = init_mmap_read::<TrigonRiskState>(TRIGON_RISK_PATH)?;
    let engine = unsafe { &*(e_mmap.as_ptr() as *const TrigonEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const TrigonRiskState) };

    let home = std::env::var("HOME").unwrap_or("/tmp".into());
    let db_dir = format!("{}/.local/share/sniper", home);
    std::fs::create_dir_all(&db_dir)?;
    let conn = Connection::open(format!("{}/trigon.db", db_dir))?;
    init_db(&conn)?;

    loop {
        let ts = Utc::now().to_rfc3339();
        let total_arbs = engine.total_arbs.load(Ordering::Acquire) as i64;
        let total_pnl = engine.total_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let daily_pnl = engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let best = engine.best_profit_bps.load(Ordering::Acquire) as f64 / 100.0;
        let scan_lat = engine.scan_latency_ns.load(Ordering::Acquire) as i64;
        let paused = risk.global_paused.load(Ordering::Acquire) as i64;

        let _ = conn.execute(
            "INSERT INTO trigon_snapshots (ts,total_arbs,total_pnl,daily_pnl,best_profit_bps,scan_latency_ns,paused) VALUES (?1,?2,?3,?4,?5,?6,?7)",
            params![ts, total_arbs, total_pnl, daily_pnl, best, scan_lat, paused],
        );

        // Log profitable opportunities
        for t in 0..TRIGON_MAX_TRIANGLES {
            let profit = engine.triangles[t].profit_bps.load(Ordering::Acquire) as f64 / 100.0;
            if profit > 0.0 {
                let leg0 = symbol_hash_to_str(risk.triangles[t].leg_symbols[0].load(Ordering::Acquire));
                let leg1 = symbol_hash_to_str(risk.triangles[t].leg_symbols[1].load(Ordering::Acquire));
                let leg2 = symbol_hash_to_str(risk.triangles[t].leg_symbols[2].load(Ordering::Acquire));
                let rate = engine.triangles[t].implied_rate.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let _ = conn.execute(
                    "INSERT INTO trigon_opportunities (ts,triangle_idx,implied_rate,profit_bps,leg0,leg1,leg2,executed) VALUES (?1,?2,?3,?4,?5,?6,?7,0)",
                    params![ts, t as i64, rate, profit, leg0, leg1, leg2],
                );
            }
        }

        // Cleanup: keep last 7 days
        let _ = conn.execute("DELETE FROM trigon_snapshots WHERE ts < datetime('now', '-7 days')", []);
        let _ = conn.execute("DELETE FROM trigon_opportunities WHERE ts < datetime('now', '-7 days')", []);

        std::thread::sleep(Duration::from_secs(30));
    }
}
