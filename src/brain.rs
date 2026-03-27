//! 🐺 BEROUN SNIPER BRAIN v10.2
//! ═══════════════════════════════
//! Permanent SQLite memory for the Sovereign Oracle.
//! CLI interface matching beroun-config pattern.
//!
//! Usage:
//!   beroun-brain init              — Create/migrate DB
//!   beroun-brain log-cycle <json>  — Store an Oracle cycle snapshot
//!   beroun-brain log-alert <json>  — Store a tactical alert
//!   beroun-brain query [--last Xh] [--regime R] [--limit N]
//!   beroun-brain analyze           — Self-analysis: optimal params per regime
//!   beroun-brain pattern <regime>  — Get learned optimal params
//!   beroun-brain stats             — Overall statistics
//!   beroun-brain context [--cycles N] — Generate Gemini prompt context

use clap::{Parser, Subcommand};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use anyhow::Result;
use std::path::PathBuf;

const DEFAULT_DB: &str = "/home/wwwenda/hft-sniper/logs/sniper.db";

#[derive(Parser)]
#[command(name = "beroun-brain", version = "10.2.0")]
#[command(about = "🐺 Sniper Brain — Permanent memory for the Sovereign Oracle")]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Path to SQLite database
    #[arg(long, default_value = DEFAULT_DB)]
    db: PathBuf,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize/migrate the database
    Init,
    /// Log an Oracle cycle snapshot
    LogCycle {
        /// JSON string with cycle data
        json: String,
    },
    /// Log a tactical alert
    LogAlert {
        /// JSON string with alert data
        json: String,
    },
    /// Query historical cycles
    Query {
        /// Filter by last N hours (e.g. "1h", "6h", "24h")
        #[arg(long)]
        last: Option<String>,
        /// Filter by regime
        #[arg(long)]
        regime: Option<String>,
        /// Max results
        #[arg(long, default_value = "20")]
        limit: u32,
    },
    /// Self-analysis: optimal parameters per regime
    Analyze,
    /// Get learned optimal params for a specific regime
    Pattern {
        regime: String,
    },
    /// Overall statistics
    Stats,
    /// Generate Gemini prompt context from permanent memory
    Context {
        /// Number of recent cycles to include
        #[arg(long, default_value = "6")]
        cycles: u32,
    },
}

// ═══ DATA STRUCTURES ═══

#[derive(Debug, Serialize, Deserialize, Default)]
struct CycleData {
    #[serde(default)] regime: String,
    #[serde(default)] grid_step: f64,
    #[serde(default)] max_position: f64,
    #[serde(default)] spread: f64,
    #[serde(default)] position: f64,
    #[serde(default)] pnl: f64,
    #[serde(default)] equity: f64,
    #[serde(default)] confidence: f64,
    #[serde(default)] fp_rate: f64,
    #[serde(default)] success_rate: f64,
    #[serde(default)] toxic_hits: i64,
    #[serde(default)] t2t_micros: i64,
    #[serde(default)] fear_greed: String,
    #[serde(default)] reasoning: String,
    #[serde(default)] tactical: String,
    #[serde(default)] escalation: String,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct AlertData {
    #[serde(default)] level: String,
    #[serde(default)] title: String,
    #[serde(default)] message: String,
}

// ═══ DATABASE ═══

fn open_db(path: &PathBuf) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
    Ok(conn)
}

fn init_db(conn: &Connection) -> Result<()> {
    conn.execute_batch("
        CREATE TABLE IF NOT EXISTS cycles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            regime      TEXT,
            grid_step   REAL,
            max_position REAL,
            spread      REAL,
            position    REAL,
            pnl         REAL,
            equity      REAL,
            confidence  REAL,
            fp_rate     REAL,
            success_rate REAL,
            toxic_hits  INTEGER,
            t2t_micros  INTEGER,
            fear_greed  TEXT,
            reasoning   TEXT,
            tactical    TEXT,
            escalation  TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            level       TEXT,
            title       TEXT,
            message     TEXT,
            resolved    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            regime      TEXT UNIQUE,
            spread_cat  TEXT,
            optimal_grid REAL,
            optimal_pos  REAL,
            avg_pnl     REAL,
            sample_count INTEGER,
            updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_cycles_ts ON cycles(timestamp);
        CREATE INDEX IF NOT EXISTS idx_cycles_regime ON cycles(regime);
        CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp);
    ")?;
    Ok(())
}

// ═══ COMMANDS ═══

fn cmd_log_cycle(conn: &Connection, json: &str) -> Result<()> {
    let d: CycleData = serde_json::from_str(json)?;
    conn.execute(
        "INSERT INTO cycles (regime, grid_step, max_position, spread, position, pnl, equity,
            confidence, fp_rate, success_rate, toxic_hits, t2t_micros, fear_greed,
            reasoning, tactical, escalation)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16)",
        params![d.regime, d.grid_step, d.max_position, d.spread, d.position, d.pnl, d.equity,
                d.confidence, d.fp_rate, d.success_rate, d.toxic_hits, d.t2t_micros,
                d.fear_greed, d.reasoning, d.tactical, d.escalation],
    )?;
    let id = conn.last_insert_rowid();

    // Auto-update patterns after each cycle
    update_patterns(conn)?;

    eprintln!("[BRAIN] Cycle #{id} logged (regime={}, grid=${:.1}, pnl=${:.4})",
        d.regime, d.grid_step, d.pnl);
    Ok(())
}

fn cmd_log_alert(conn: &Connection, json: &str) -> Result<()> {
    let d: AlertData = serde_json::from_str(json)?;
    conn.execute(
        "INSERT INTO alerts (level, title, message) VALUES (?1, ?2, ?3)",
        params![d.level, d.title, d.message],
    )?;
    eprintln!("[BRAIN] Alert logged: {} {}", d.level, d.title);
    Ok(())
}

fn cmd_query(conn: &Connection, last: Option<String>, regime: Option<String>, limit: u32) -> Result<()> {
    let mut sql = String::from("SELECT id, timestamp, regime, grid_step, max_position, spread, position, pnl, equity, confidence, fp_rate, success_rate, toxic_hits, t2t_micros, reasoning, tactical FROM cycles WHERE 1=1");

    if let Some(ref hours) = last {
        let h: f64 = hours.trim_end_matches('h').parse().unwrap_or(1.0);
        sql.push_str(&format!(" AND timestamp >= datetime('now', 'localtime', '-{} hours')", h));
    }
    if regime.is_some() {
        sql.push_str(" AND regime = ?1");
    }
    sql.push_str(&format!(" ORDER BY id DESC LIMIT {}", limit));

    let mut stmt = conn.prepare(&sql)?;
    let mut results: Vec<serde_json::Value> = Vec::new();

    let map_row = |row: &rusqlite::Row| -> rusqlite::Result<serde_json::Value> {
        Ok(serde_json::json!({
            "id": row.get::<_, i64>(0)?,
            "timestamp": row.get::<_, String>(1)?,
            "regime": row.get::<_, String>(2).unwrap_or_default(),
            "grid_step": row.get::<_, f64>(3)?,
            "max_position": row.get::<_, f64>(4)?,
            "spread": row.get::<_, f64>(5)?,
            "position": row.get::<_, f64>(6)?,
            "pnl": row.get::<_, f64>(7)?,
            "equity": row.get::<_, f64>(8)?,
            "confidence": row.get::<_, f64>(9)?,
            "fp_rate": row.get::<_, f64>(10)?,
            "success_rate": row.get::<_, f64>(11)?,
            "toxic_hits": row.get::<_, i64>(12)?,
            "t2t_micros": row.get::<_, i64>(13)?,
            "reasoning": row.get::<_, String>(14).unwrap_or_default(),
            "tactical": row.get::<_, String>(15).unwrap_or_default(),
        }))
    };

    if let Some(ref r) = regime {
        let rows = stmt.query_map(params![r], map_row)?;
        for row in rows.flatten() {
            results.push(row);
        }
    } else {
        let rows = stmt.query_map([], map_row)?;
        for row in rows.flatten() {
            results.push(row);
        }
    }

    println!("{}", serde_json::to_string_pretty(&results)?);
    Ok(())
}

fn cmd_analyze(conn: &Connection) -> Result<()> {
    let mut stmt = conn.prepare(
        "SELECT regime,
                COUNT(*) as cnt,
                AVG(pnl) as avg_pnl,
                AVG(grid_step) as avg_grid,
                AVG(max_position) as avg_pos,
                AVG(spread) as avg_spread,
                AVG(confidence) as avg_conf,
                AVG(toxic_hits) as avg_toxic,
                MIN(pnl) as min_pnl,
                MAX(pnl) as max_pnl
         FROM cycles
         GROUP BY regime
         ORDER BY cnt DESC"
    )?;

    let rows = stmt.query_map([], |row| {
        Ok(serde_json::json!({
            "regime": row.get::<_, String>(0)?,
            "cycles": row.get::<_, i64>(1)?,
            "avg_pnl": row.get::<_, f64>(2)?,
            "avg_grid": row.get::<_, f64>(3)?,
            "avg_position": row.get::<_, f64>(4)?,
            "avg_spread": row.get::<_, f64>(5)?,
            "avg_confidence": row.get::<_, f64>(6)?,
            "avg_toxic": row.get::<_, f64>(7)?,
            "min_pnl": row.get::<_, f64>(8)?,
            "max_pnl": row.get::<_, f64>(9)?,
        }))
    })?;

    let results: Vec<_> = rows.filter_map(|r| r.ok()).collect();
    println!("{}", serde_json::to_string_pretty(&results)?);
    Ok(())
}

fn update_patterns(conn: &Connection) -> Result<()> {
    // Find optimal grid per regime (grid that produced best avg PnL)
    conn.execute_batch("
        INSERT OR REPLACE INTO patterns (regime, optimal_grid, optimal_pos, avg_pnl, sample_count, updated_at)
        SELECT
            regime,
            grid_step as optimal_grid,
            AVG(max_position) as optimal_pos,
            AVG(pnl) as avg_pnl,
            COUNT(*) as sample_count,
            datetime('now', 'localtime')
        FROM cycles
        WHERE regime IS NOT NULL AND regime != ''
        GROUP BY regime
        HAVING COUNT(*) >= 3
    ")?;
    Ok(())
}

fn cmd_pattern(conn: &Connection, regime: &str) -> Result<()> {
    let mut stmt = conn.prepare(
        "SELECT regime, optimal_grid, optimal_pos, avg_pnl, sample_count, updated_at
         FROM patterns WHERE UPPER(regime) = UPPER(?1)"
    )?;

    let result = stmt.query_row(params![regime], |row| {
        Ok(serde_json::json!({
            "regime": row.get::<_, String>(0)?,
            "optimal_grid": row.get::<_, f64>(1)?,
            "optimal_position": row.get::<_, f64>(2)?,
            "avg_pnl_per_cycle": row.get::<_, f64>(3)?,
            "sample_count": row.get::<_, i64>(4)?,
            "last_updated": row.get::<_, String>(5)?,
        }))
    });

    match result {
        Ok(r) => println!("{}", serde_json::to_string_pretty(&r)?),
        Err(_) => println!("{{\"error\": \"No pattern data for regime '{}'. Need >= 3 cycles.\"}}", regime),
    }
    Ok(())
}

fn cmd_stats(conn: &Connection) -> Result<()> {
    let total_cycles: i64 = conn.query_row("SELECT COUNT(*) FROM cycles", [], |r| r.get(0))?;
    let total_alerts: i64 = conn.query_row("SELECT COUNT(*) FROM alerts", [], |r| r.get(0))?;

    let (first_ts, last_ts): (String, String) = if total_cycles > 0 {
        let first: String = conn.query_row("SELECT timestamp FROM cycles ORDER BY id ASC LIMIT 1", [], |r| r.get(0))?;
        let last: String = conn.query_row("SELECT timestamp FROM cycles ORDER BY id DESC LIMIT 1", [], |r| r.get(0))?;
        (first, last)
    } else {
        ("N/A".into(), "N/A".into())
    };

    let avg_pnl: f64 = conn.query_row("SELECT COALESCE(AVG(pnl), 0) FROM cycles", [], |r| r.get(0))?;
    let total_pnl_change: f64 = if total_cycles > 1 {
        let first_pnl: f64 = conn.query_row("SELECT pnl FROM cycles ORDER BY id ASC LIMIT 1", [], |r| r.get(0)).unwrap_or(0.0);
        let last_pnl: f64 = conn.query_row("SELECT pnl FROM cycles ORDER BY id DESC LIMIT 1", [], |r| r.get(0)).unwrap_or(0.0);
        last_pnl - first_pnl
    } else { 0.0 };

    let escalations: i64 = conn.query_row(
        "SELECT COUNT(*) FROM cycles WHERE escalation != 'hold' AND escalation != ''", [], |r| r.get(0))?;

    // Regime distribution
    let mut stmt = conn.prepare("SELECT regime, COUNT(*) FROM cycles GROUP BY regime ORDER BY COUNT(*) DESC")?;
    let regimes: Vec<(String, i64)> = stmt.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
    })?.filter_map(|r| r.ok()).collect();

    let regime_str: String = regimes.iter()
        .map(|(r, c)| format!("{}={}", r, c))
        .collect::<Vec<_>>().join(", ");

    let result = serde_json::json!({
        "total_cycles": total_cycles,
        "total_alerts": total_alerts,
        "first_cycle": first_ts,
        "last_cycle": last_ts,
        "avg_pnl_per_cycle": format!("${:.6}", avg_pnl),
        "total_pnl_change": format!("${:.6}", total_pnl_change),
        "escalation_events": escalations,
        "regime_distribution": regime_str,
        "db_size_bytes": std::fs::metadata(DEFAULT_DB).map(|m| m.len()).unwrap_or(0),
    });

    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

fn cmd_context(conn: &Connection, cycles: u32) -> Result<()> {
    // Generate rich Gemini prompt context from permanent memory
    let total: i64 = conn.query_row("SELECT COUNT(*) FROM cycles", [], |r| r.get(0))?;

    if total == 0 {
        println!("No history available (first session).");
        return Ok(());
    }

    let mut output = String::new();
    output.push_str(&format!("Total cycles in permanent memory: {}\n", total));

    // Session info
    let first_ts: String = conn.query_row("SELECT timestamp FROM cycles ORDER BY id ASC LIMIT 1", [], |r| r.get(0))?;
    output.push_str(&format!("Recording since: {}\n\n", first_ts));

    // Performance by regime (from patterns table)
    output.push_str("Historical performance by regime:\n");
    let mut stmt = conn.prepare(
        "SELECT regime, sample_count, avg_pnl, optimal_grid, optimal_pos
         FROM patterns ORDER BY sample_count DESC"
    )?;
    let patterns = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, i64>(1)?,
            r.get::<_, f64>(2)?,
            r.get::<_, f64>(3)?,
            r.get::<_, f64>(4)?,
        ))
    })?;
    for p in patterns.flatten() {
        output.push_str(&format!(
            "  {}: {} cycles, avg PnL ${:.6}/cycle, grid ${:.1}, pos {:.4} BTC\n",
            p.0, p.1, p.2, p.3, p.4
        ));
    }

    // Recent cycles
    output.push_str(&format!("\nRecent {} cycles:\n", cycles));
    let mut stmt = conn.prepare(&format!(
        "SELECT timestamp, regime, grid_step, pnl, position, confidence, toxic_hits, spread, tactical
         FROM cycles ORDER BY id DESC LIMIT {}", cycles
    ))?;
    let recent = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1).unwrap_or_default(),
            r.get::<_, f64>(2)?,
            r.get::<_, f64>(3)?,
            r.get::<_, f64>(4)?,
            r.get::<_, f64>(5)?,
            r.get::<_, i64>(6)?,
            r.get::<_, f64>(7)?,
            r.get::<_, String>(8).unwrap_or_default(),
        ))
    })?;
    for (i, row) in recent.flatten().enumerate() {
        let age = (i + 1) * 5;
        output.push_str(&format!(
            "  -{:>3}min: {} Grid=${:.1} PnL=${:.4} Pos={:.5}BTC Conf={:.0}% Toxic={} Spread=${:.1}\n",
            age, row.1, row.2, row.3, row.4, row.5 * 100.0, row.6, row.7
        ));
    }

    // Trend analysis from last 6 cycles
    let mut stmt = conn.prepare("SELECT pnl, position, confidence FROM cycles ORDER BY id DESC LIMIT 6")?;
    let trend_data: Vec<(f64, f64, f64)> = stmt.query_map([], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?))
    })?.filter_map(|r| r.ok()).collect();

    if trend_data.len() >= 3 {
        let pnl_trend = analyze_trend(&trend_data.iter().map(|d| d.0).collect::<Vec<_>>());
        let pos_trend = analyze_trend(&trend_data.iter().map(|d| d.1).collect::<Vec<_>>());
        let conf_trend = analyze_trend(&trend_data.iter().map(|d| d.2).collect::<Vec<_>>());
        output.push_str(&format!("\nTrends (last 30min): PnL={} Position={} Confidence={}\n",
            pnl_trend, pos_trend, conf_trend));
    }

    // Unresolved alerts
    let unresolved: i64 = conn.query_row(
        "SELECT COUNT(*) FROM alerts WHERE resolved = 0 AND timestamp >= datetime('now', 'localtime', '-1 hour')",
        [], |r| r.get(0))?;
    if unresolved > 0 {
        output.push_str(&format!("\n⚠️ {} unresolved alerts in last hour\n", unresolved));
    }

    print!("{}", output);
    Ok(())
}

fn analyze_trend(values: &[f64]) -> &'static str {
    if values.len() < 3 { return "insufficient"; }
    // Values are newest-first, reverse for chronological
    let chronological: Vec<f64> = values.iter().rev().cloned().collect();
    let rising = chronological.windows(2).all(|w| w[1] >= w[0] - 0.0001);
    let falling = chronological.windows(2).all(|w| w[1] <= w[0] + 0.0001);
    if rising { "rising ↑" } else if falling { "falling ↓" } else { "stable ↔" }
}

// ═══ MAIN ═══

fn main() -> Result<()> {
    let cli = Cli::parse();
    let conn = open_db(&cli.db)?;

    // Always ensure tables exist
    init_db(&conn)?;

    match cli.command {
        Commands::Init => {
            println!("🧠 Sniper Brain initialized: {}", cli.db.display());
            println!("   Tables: cycles, alerts, patterns");
        }
        Commands::LogCycle { json } => cmd_log_cycle(&conn, &json)?,
        Commands::LogAlert { json } => cmd_log_alert(&conn, &json)?,
        Commands::Query { last, regime, limit } => cmd_query(&conn, last, regime, limit)?,
        Commands::Analyze => cmd_analyze(&conn)?,
        Commands::Pattern { regime } => cmd_pattern(&conn, &regime)?,
        Commands::Stats => cmd_stats(&conn)?,
        Commands::Context { cycles } => cmd_context(&conn, cycles)?,
    }

    Ok(())
}
