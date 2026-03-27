// 🌙 Moonshot Brain — SQLite Analytics & Trade History
// Sniper Armada · Bot #2 · Persistence Layer
//
// Periodically snapshots mmap state into SQLite for:
// - Trade fill history and spike analytics
// - AI decision logging (pair selection, rotations)
// - Daily PnL tracking and win-rate calculations

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;

use memmap2::MmapMut;
use rusqlite::{Connection, params};
use anyhow::Result;
use chrono::Utc;

use sniper_types::moonshot_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

fn init_mmap_read<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn init_db(conn: &Connection) -> Result<()> {
    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS moonshot_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            active_pairs INTEGER,
            total_pnl REAL,
            daily_pnl REAL,
            total_fills INTEGER,
            paused INTEGER
        );

        CREATE TABLE IF NOT EXISTS moonshot_pair_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            idx INTEGER,
            symbol TEXT,
            bid REAL,
            ask REAL,
            position REAL,
            pnl REAL,
            fills INTEGER,
            drop_pct REAL,
            tp_pct REAL,
            order_usd REAL,
            buy_level REAL
        );

        CREATE TABLE IF NOT EXISTS moonshot_ai_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            pairs_json TEXT,
            reasoning TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON moonshot_snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_pair_snapshots_ts ON moonshot_pair_snapshots(ts);
    ")?;
    Ok(())
}

fn main() -> Result<()> {
    println!("🧠 Moonshot Brain — SQLite Analytics Engine");

    let e_mmap = init_mmap_read::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH)?;
    let r_mmap = init_mmap_read::<MoonshotRiskState>(MOONSHOT_RISK_PATH)?;
    let engine = unsafe { &*(e_mmap.as_ptr() as *const MoonshotEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const MoonshotRiskState) };

    // DB path: ~/.local/share/sniper/moonshot.db
    let db_dir = dirs_path();
    std::fs::create_dir_all(&db_dir)?;
    let db_path = format!("{}/moonshot.db", db_dir);
    let conn = Connection::open(&db_path)?;
    init_db(&conn)?;

    println!("📊 Database: {}", db_path);

    // Snapshot every 30 seconds
    loop {
        let ts = Utc::now().to_rfc3339();
        let paused = risk.global_paused.load(Ordering::Acquire);
        let daily_pnl = engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let total_fills = engine.total_fills.load(Ordering::Acquire);

        let mut active_pairs = 0u32;
        let mut total_pnl = 0.0f64;

        for i in 0..MOONSHOT_MAX_PAIRS {
            let sym_hash = risk.pairs[i].symbol_hash.load(Ordering::Acquire);
            let active = engine.pairs[i].active.load(Ordering::Acquire);

            if sym_hash != 0 && active == 1 {
                active_pairs += 1;
                let symbol = symbol_hash_to_str(sym_hash);
                let bid = engine.pairs[i].best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let ask = engine.pairs[i].best_ask.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let pos = engine.pairs[i].net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let pnl = engine.pairs[i].realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let fills = engine.pairs[i].fill_count.load(Ordering::Acquire);
                let drop_pct = risk.pairs[i].m_shot_price_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                let tp_pct = risk.pairs[i].tp_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                let order_usd = risk.pairs[i].order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                let buy_level = engine.pairs[i].buy_order_price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

                total_pnl += pnl;

                let _ = conn.execute(
                    "INSERT INTO moonshot_pair_snapshots (ts, idx, symbol, bid, ask, position, pnl, fills, drop_pct, tp_pct, order_usd, buy_level) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)",
                    params![ts, i as i64, symbol, bid, ask, pos, pnl, fills as i64, drop_pct, tp_pct, order_usd, buy_level],
                );
            }
        }

        let _ = conn.execute(
            "INSERT INTO moonshot_snapshots (ts, active_pairs, total_pnl, daily_pnl, total_fills, paused) VALUES (?1,?2,?3,?4,?5,?6)",
            params![ts, active_pairs, total_pnl, daily_pnl, total_fills as i64, paused as i64],
        );

        // Cleanup: keep last 7 days
        let _ = conn.execute(
            "DELETE FROM moonshot_snapshots WHERE ts < datetime('now', '-7 days')",
            [],
        );
        let _ = conn.execute(
            "DELETE FROM moonshot_pair_snapshots WHERE ts < datetime('now', '-7 days')",
            [],
        );

        std::thread::sleep(Duration::from_secs(30));
    }
}

fn dirs_path() -> String {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/home/wwwenda".to_string());
    format!("{}/.local/share/sniper", home)
}
