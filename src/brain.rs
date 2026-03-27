//! 🐺 BEROUN SNIPER BRAIN v10.3 "Neural Cross"
//! ═══════════════════════════════════════════════
//! Permanent SQLite memory + self-learning backtest engine.
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
//!   beroun-brain backtest [--days N]  — Run nightly Neural Cross analysis
//!   beroun-brain lessons [--regime R] — Get active learned lessons
//!   beroun-brain worst-cycles [--n 5] — Export worst cycles for AI critique

use clap::{Parser, Subcommand};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use anyhow::Result;
use std::path::PathBuf;

const DEFAULT_DB: &str = "/home/wwwenda/hft-sniper/logs/sniper.db";

#[derive(Parser)]
#[command(name = "beroun-brain", version = "10.3.0")]
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
    /// Run Neural Cross backtest (nightly self-analysis)
    Backtest {
        /// Analyze last N days
        #[arg(long, default_value = "1")]
        days: u32,
        /// Dry-run: show what would happen without writing
        #[arg(long)]
        dry_run: bool,
    },
    /// Get active learned lessons
    Lessons {
        /// Filter by regime
        #[arg(long)]
        regime: Option<String>,
        /// Only active lessons
        #[arg(long, default_value = "true")]
        active: bool,
    },
    /// Export worst cycles for AI self-critique
    WorstCycles {
        /// Number of worst cycles
        #[arg(long, default_value = "5")]
        n: u32,
        /// Hours to look back
        #[arg(long, default_value = "24")]
        hours: u32,
    },
    /// Save a lesson from AI coach
    SaveLesson {
        /// JSON lesson data
        json: String,
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

        CREATE TABLE IF NOT EXISTS experiments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT DEFAULT (datetime('now', 'localtime')),
            cycle_id        INTEGER REFERENCES cycles(id),
            regime          TEXT NOT NULL,
            original_grid   REAL NOT NULL,
            proposed_grid   REAL NOT NULL,
            original_pnl    REAL NOT NULL,
            simulated_pnl   REAL NOT NULL,
            learning_delta  REAL NOT NULL,
            method          TEXT DEFAULT 'grid_sweep'
        );

        CREATE TABLE IF NOT EXISTS lessons (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT DEFAULT (datetime('now', 'localtime')),
            regime          TEXT NOT NULL,
            rule_type       TEXT NOT NULL,
            condition       TEXT NOT NULL,
            action          TEXT NOT NULL,
            reasoning       TEXT,
            confidence      REAL DEFAULT 0.3,
            sample_count    INTEGER DEFAULT 1,
            last_validated  TEXT,
            source          TEXT DEFAULT 'backtest',
            active          INTEGER DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_experiments_regime ON experiments(regime);
        CREATE INDEX IF NOT EXISTS idx_lessons_regime ON lessons(regime);
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
    init_db(&conn)?;

    match cli.command {
        Commands::Init => {
            println!("🧠 Sniper Brain v10.3 initialized: {}", cli.db.display());
            println!("   Tables: cycles, alerts, patterns, experiments, lessons");
        }
        Commands::LogCycle { json } => cmd_log_cycle(&conn, &json)?,
        Commands::LogAlert { json } => cmd_log_alert(&conn, &json)?,
        Commands::Query { last, regime, limit } => cmd_query(&conn, last, regime, limit)?,
        Commands::Analyze => cmd_analyze(&conn)?,
        Commands::Pattern { regime } => cmd_pattern(&conn, &regime)?,
        Commands::Stats => cmd_stats(&conn)?,
        Commands::Context { cycles } => cmd_context(&conn, cycles)?,
        Commands::Backtest { days, dry_run } => cmd_backtest(&conn, days, dry_run)?,
        Commands::Lessons { regime, active } => cmd_lessons(&conn, regime, active)?,
        Commands::WorstCycles { n, hours } => cmd_worst_cycles(&conn, n, hours)?,
        Commands::SaveLesson { json } => cmd_save_lesson(&conn, &json)?,
    }

    Ok(())
}

// ═══ v10.3 NEURAL CROSS ═══

fn cmd_backtest(conn: &Connection, days: u32, dry_run: bool) -> Result<()> {
    let hours = days * 24;
    let time_filter = format!("-{} hours", hours);

    // Count available cycles
    let total: i64 = conn.query_row(
        "SELECT COUNT(*) FROM cycles WHERE timestamp > datetime('now', 'localtime', ?1)",
        params![time_filter], |r| r.get(0),
    )?;

    if total < 10 {
        let result = serde_json::json!({
            "status": "insufficient_data",
            "cycles_available": total,
            "minimum_required": 10,
            "message": "Need at least 10 cycles for meaningful analysis"
        });
        println!("{}", serde_json::to_string_pretty(&result)?);
        return Ok(());
    }

    // Get unique regimes in the period
    let mut regime_stmt = conn.prepare(
        "SELECT DISTINCT regime FROM cycles WHERE timestamp > datetime('now', 'localtime', ?1) AND regime IS NOT NULL"
    )?;
    let regimes: Vec<String> = regime_stmt.query_map(params![time_filter], |r| r.get(0))?
        .filter_map(|r| r.ok())
        .collect();

    let mut all_findings = Vec::new();
    let mut lessons_generated = 0i32;
    let mut lessons_updated = 0i32;

    for regime in &regimes {
        // Winners vs losers analysis
        let winner_stats: (f64, f64, f64, f64, i64) = conn.query_row(
            "SELECT COALESCE(AVG(grid_step),0), COALESCE(AVG(ABS(position)),0), \
             COALESCE(AVG(toxic_hits),0), COALESCE(AVG(confidence),0), COUNT(*) \
             FROM cycles WHERE regime=?1 AND pnl > 0 AND timestamp > datetime('now','localtime',?2)",
            params![regime, time_filter],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )?;

        let loser_stats: (f64, f64, f64, f64, i64) = conn.query_row(
            "SELECT COALESCE(AVG(grid_step),0), COALESCE(AVG(ABS(position)),0), \
             COALESCE(AVG(toxic_hits),0), COALESCE(AVG(confidence),0), COUNT(*) \
             FROM cycles WHERE regime=?1 AND pnl < 0 AND timestamp > datetime('now','localtime',?2)",
            params![regime, time_filter],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )?
        ;

        let neutral_count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM cycles WHERE regime=?1 AND pnl = 0 AND timestamp > datetime('now','localtime',?2)",
            params![regime, time_filter], |r| r.get(0),
        )?;

        let finding = serde_json::json!({
            "regime": regime,
            "winners": {
                "count": winner_stats.4,
                "avg_grid": format!("{:.1}", winner_stats.0),
                "avg_position": format!("{:.6}", winner_stats.1),
                "avg_toxic": format!("{:.0}", winner_stats.2),
                "avg_confidence": format!("{:.2}", winner_stats.3),
            },
            "losers": {
                "count": loser_stats.4,
                "avg_grid": format!("{:.1}", loser_stats.0),
                "avg_position": format!("{:.6}", loser_stats.1),
                "avg_toxic": format!("{:.0}", loser_stats.2),
                "avg_confidence": format!("{:.2}", loser_stats.3),
            },
            "neutral_count": neutral_count,
        });
        all_findings.push(finding);

        // Skip lesson generation if too few samples
        if winner_stats.4 < 3 || loser_stats.4 < 3 {
            continue;
        }

        // Generate lessons from statistical differences
        // Rule 1: Grid floor (if losers had significantly lower grid)
        if loser_stats.0 > 0.0 && winner_stats.0 > loser_stats.0 * 1.3 {
            let grid_floor = winner_stats.0 * 0.85; // 85% of winner avg
            let condition = serde_json::json!({"always": true}).to_string();
            let action = serde_json::json!({"grid_min": (grid_floor * 10.0).round() / 10.0}).to_string();
            let samples = winner_stats.4 + loser_stats.4;
            let confidence = (samples as f64 / 50.0).min(0.9).max(0.3);

            if !dry_run {
                let existing: Option<i64> = conn.query_row(
                    "SELECT id FROM lessons WHERE regime=?1 AND rule_type='grid_floor' AND active=1",
                    params![regime], |r| r.get(0),
                ).ok();

                if let Some(id) = existing {
                    conn.execute(
                        "UPDATE lessons SET condition=?1, action=?2, confidence=?3, \
                         sample_count=sample_count+?4, last_validated=datetime('now','localtime') \
                         WHERE id=?5",
                        params![condition, action, confidence, samples, id],
                    )?;
                    lessons_updated += 1;
                } else {
                    conn.execute(
                        "INSERT INTO lessons (regime, rule_type, condition, action, reasoning, confidence, sample_count, source) \
                         VALUES (?1, 'grid_floor', ?2, ?3, ?4, ?5, ?6, 'backtest')",
                        params![regime, condition, action,
                                format!("Winners avg grid ${:.1} vs losers ${:.1}", winner_stats.0, loser_stats.0),
                                confidence, samples],
                    )?;
                    lessons_generated += 1;
                }
            }
        }

        // Rule 2: Position cap (if losers had larger positions)
        if loser_stats.1 > 0.0 && loser_stats.1 > winner_stats.1 * 1.4 {
            let pos_cap = winner_stats.1 * 1.1;
            let condition = serde_json::json!({"always": true}).to_string();
            let action = serde_json::json!({"max_position": (pos_cap * 10000.0).round() / 10000.0}).to_string();
            let samples = winner_stats.4 + loser_stats.4;
            let confidence = (samples as f64 / 50.0).min(0.85).max(0.3);

            if !dry_run {
                let existing: Option<i64> = conn.query_row(
                    "SELECT id FROM lessons WHERE regime=?1 AND rule_type='position_cap' AND active=1",
                    params![regime], |r| r.get(0),
                ).ok();

                if let Some(id) = existing {
                    conn.execute(
                        "UPDATE lessons SET condition=?1, action=?2, confidence=?3, \
                         sample_count=sample_count+?4, last_validated=datetime('now','localtime') \
                         WHERE id=?5",
                        params![condition, action, confidence, samples, id],
                    )?;
                    lessons_updated += 1;
                } else {
                    conn.execute(
                        "INSERT INTO lessons (regime, rule_type, condition, action, reasoning, confidence, sample_count, source) \
                         VALUES (?1, 'position_cap', ?2, ?3, ?4, ?5, ?6, 'backtest')",
                        params![regime, condition, action,
                                format!("Losers avg pos {:.5} vs winners {:.5}", loser_stats.1, winner_stats.1),
                                confidence, samples],
                    )?;
                    lessons_generated += 1;
                }
            }
        }

        // Rule 3: Toxic avoidance (if losers had much higher toxic hits)
        if loser_stats.2 > winner_stats.2 * 1.8 && loser_stats.2 > 100.0 {
            let toxic_threshold = ((winner_stats.2 + loser_stats.2) / 2.0).round();
            let condition = serde_json::json!({"toxic_above": toxic_threshold}).to_string();
            let action = serde_json::json!({"grid_multiply": 1.4, "reduce_position": true}).to_string();
            let samples = winner_stats.4 + loser_stats.4;
            let confidence = (samples as f64 / 40.0).min(0.85).max(0.3);

            if !dry_run {
                let existing: Option<i64> = conn.query_row(
                    "SELECT id FROM lessons WHERE regime=?1 AND rule_type='toxic_avoidance' AND active=1",
                    params![regime], |r| r.get(0),
                ).ok();

                if let Some(id) = existing {
                    conn.execute(
                        "UPDATE lessons SET condition=?1, action=?2, confidence=?3, \
                         sample_count=sample_count+?4, last_validated=datetime('now','localtime') \
                         WHERE id=?5",
                        params![condition, action, confidence, samples, id],
                    )?;
                    lessons_updated += 1;
                } else {
                    conn.execute(
                        "INSERT INTO lessons (regime, rule_type, condition, action, reasoning, confidence, sample_count, source) \
                         VALUES (?1, 'toxic_avoidance', ?2, ?3, ?4, ?5, ?6, 'backtest')",
                        params![regime, condition, action,
                                format!("Losers avg toxic {:.0} vs winners {:.0}", loser_stats.2, winner_stats.2),
                                confidence, samples],
                    )?;
                    lessons_generated += 1;
                }
            }
        }
    }

    // Decay old lessons not validated recently
    if !dry_run {
        conn.execute(
            "UPDATE lessons SET confidence = confidence - 0.05 \
             WHERE last_validated < datetime('now', 'localtime', '-3 days') AND active=1",
            [],
        )?;
        // Deactivate low-confidence lessons
        conn.execute(
            "UPDATE lessons SET active=0 WHERE confidence < 0.15 AND sample_count > 5",
            [],
        )?;
    }

    let result = serde_json::json!({
        "status": if dry_run { "dry_run" } else { "completed" },
        "period_days": days,
        "cycles_analyzed": total,
        "regimes": regimes,
        "findings": all_findings,
        "lessons_generated": lessons_generated,
        "lessons_updated": lessons_updated,
    });
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

fn cmd_lessons(conn: &Connection, regime: Option<String>, active_only: bool) -> Result<()> {
    let query = match (&regime, active_only) {
        (Some(r), true) => format!(
            "SELECT id, regime, rule_type, condition, action, reasoning, confidence, sample_count, source, created_at \
             FROM lessons WHERE regime='{}' AND active=1 ORDER BY confidence DESC", r),
        (Some(r), false) => format!(
            "SELECT id, regime, rule_type, condition, action, reasoning, confidence, sample_count, source, created_at \
             FROM lessons WHERE regime='{}' ORDER BY confidence DESC", r),
        (None, true) => "SELECT id, regime, rule_type, condition, action, reasoning, confidence, sample_count, source, created_at \
             FROM lessons WHERE active=1 ORDER BY confidence DESC".to_string(),
        (None, false) => "SELECT id, regime, rule_type, condition, action, reasoning, confidence, sample_count, source, created_at \
             FROM lessons ORDER BY confidence DESC".to_string(),
    };

    let mut stmt = conn.prepare(&query)?;
    let mut rows = Vec::new();
    let mut row_iter = stmt.query([])?;
    while let Some(row) = row_iter.next()? {
        let entry = serde_json::json!({
            "id": row.get::<_, i64>(0)?,
            "regime": row.get::<_, String>(1)?,
            "rule_type": row.get::<_, String>(2)?,
            "condition": row.get::<_, String>(3)?,
            "action": row.get::<_, String>(4)?,
            "reasoning": row.get::<_, Option<String>>(5)?,
            "confidence": row.get::<_, f64>(6)?,
            "sample_count": row.get::<_, i64>(7)?,
            "source": row.get::<_, String>(8)?,
            "created_at": row.get::<_, String>(9)?,
        });
        rows.push(entry);
    }
    println!("{}", serde_json::to_string_pretty(&rows)?);
    Ok(())
}

fn cmd_worst_cycles(conn: &Connection, n: u32, hours: u32) -> Result<()> {
    let time_filter = format!("-{} hours", hours);
    let mut stmt = conn.prepare(
        "SELECT id, timestamp, regime, grid_step, pnl, position, toxic_hits, confidence, \
         reasoning, tactical, spread, t2t_micros, fear_greed \
         FROM cycles WHERE timestamp > datetime('now', 'localtime', ?1) AND pnl < 0 \
         ORDER BY pnl ASC LIMIT ?2"
    )?;

    let mut rows = Vec::new();
    let mut row_iter = stmt.query(params![time_filter, n])?;
    while let Some(row) = row_iter.next()? {
        let entry = serde_json::json!({
            "cycle_id": row.get::<_, i64>(0)?,
            "timestamp": row.get::<_, String>(1)?,
            "regime": row.get::<_, Option<String>>(2)?,
            "grid_step": row.get::<_, f64>(3)?,
            "pnl": row.get::<_, f64>(4)?,
            "position": row.get::<_, f64>(5)?,
            "toxic_hits": row.get::<_, i64>(6)?,
            "confidence": row.get::<_, f64>(7)?,
            "reasoning_at_the_time": row.get::<_, Option<String>>(8)?,
            "tactical_at_the_time": row.get::<_, Option<String>>(9)?,
            "spread": row.get::<_, f64>(10)?,
            "t2t_micros": row.get::<_, i64>(11)?,
            "fear_greed": row.get::<_, Option<String>>(12)?,
        });
        rows.push(entry);
    }
    println!("{}", serde_json::to_string_pretty(&rows)?);
    Ok(())
}

#[derive(Debug, Deserialize)]
struct LessonInput {
    regime: String,
    rule_type: String,
    condition: serde_json::Value,
    action: serde_json::Value,
    reasoning: String,
    #[serde(default = "default_confidence")]
    confidence: f64,
}

fn default_confidence() -> f64 { 0.5 }

fn cmd_save_lesson(conn: &Connection, json: &str) -> Result<()> {
    let l: LessonInput = serde_json::from_str(json)?;

    // Check if similar lesson already exists
    let existing: Option<i64> = conn.query_row(
        "SELECT id FROM lessons WHERE regime=?1 AND rule_type=?2 AND active=1",
        params![l.regime, l.rule_type], |r| r.get(0),
    ).ok();

    if let Some(id) = existing {
        conn.execute(
            "UPDATE lessons SET condition=?1, action=?2, reasoning=?3, confidence=?4, \
             sample_count=sample_count+1, last_validated=datetime('now','localtime'), source='coach' \
             WHERE id=?5",
            params![l.condition.to_string(), l.action.to_string(), l.reasoning, l.confidence, id],
        )?;
        println!("{{\"status\": \"updated\", \"id\": {}}}", id);
    } else {
        conn.execute(
            "INSERT INTO lessons (regime, rule_type, condition, action, reasoning, confidence, source) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'coach')",
            params![l.regime, l.rule_type, l.condition.to_string(), l.action.to_string(),
                    l.reasoning, l.confidence],
        )?;
        let id = conn.last_insert_rowid();
        println!("{{\"status\": \"created\", \"id\": {}}}", id);
    }
    Ok(())
}
