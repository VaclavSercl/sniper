// ═══════════════════════════════════════════════════════════
// 📢 AsyncNotifier — Unified Telegram + log alert pipeline
// Part of sniper-shared crate (runtime feature)
//
// Eliminates 4× duplicate implementations across bots.
// Each bot passes its emoji + name at construction time.
// All I/O happens on a dedicated tokio task (non-blocking).
// ═══════════════════════════════════════════════════════════

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::time::{Duration, SystemTime};

use tokio::sync::mpsc;
use tracing::info;

/// A bot event for the notification pipeline.
/// Simple variant: just a string message.
/// Trade variant: structured trade data for rich logging.
pub enum BotEvent {
    Alert(String),
    Trade {
        pair: String,
        direction: String,
        gross_bps: f64,
        net_bps: f64,
        size_usd: f64,
    },
    Signal {
        pair: String,
        direction: String,
        gross_bps: f64,
    },
}

/// Async notification pipeline — keeps hot path clean of I/O.
///
/// Usage:
/// ```no_run
/// let notifier = AsyncNotifier::new("moonshot", "🌙");
/// notifier.alert("System online".to_string());
/// notifier.trade("tBTCUSD".into(), "BUY".into(), 15.0, 5.0, 50.0);
/// ```
pub struct AsyncNotifier {
    tx: mpsc::UnboundedSender<BotEvent>,
}

impl AsyncNotifier {
    /// Create a new notifier for the given bot.
    /// 
    /// - `bot_name`: lowercase name (e.g., "moonshot", "grid", "trigon", "nexus")
    /// - `bot_emoji`: display emoji (e.g., "🌙", "📐", "🔺", "🪐")
    pub fn new(bot_name: &str, bot_emoji: &str) -> Self {
        let (tx, mut rx) = mpsc::unbounded_channel::<BotEvent>();
        let token = std::env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default();
        let chat_id = std::env::var("TELEGRAM_CHAT_ID").unwrap_or_default();
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .unwrap_or_default();

        // Resolve log paths relative to binary location
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        let log_dir = exe_dir.join(format!("../../{}/logs", bot_name));
        let _ = std::fs::create_dir_all(&log_dir);
        let alerts_log = log_dir.join("alerts.log");
        let trades_log = log_dir.join("trades.log");

        let name_upper = bot_name.to_uppercase();
        let emoji = bot_emoji.to_string();

        tokio::spawn(async move {
            let mut signals_count: u64 = 0;
            let mut trades_count: u64 = 0;
            let mut pnl_bps_total: f64 = 0.0;

            while let Some(event) = rx.recv().await {
                match event {
                    BotEvent::Alert(msg) => {
                        info!(event = "async_alert", bot = %name_upper, message = %msg);
                        if let Ok(mut file) = OpenOptions::new()
                            .create(true).append(true).open(&alerts_log)
                        {
                            let _ = writeln!(file, "[{:?}] {}", SystemTime::now(), msg);
                        }
                        if !token.is_empty() && !chat_id.is_empty() {
                            let url = format!(
                                "https://api.telegram.org/bot{}/sendMessage",
                                token
                            );
                            let _ = client
                                .post(url)
                                .json(&serde_json::json!({
                                    "chat_id": chat_id,
                                    "text": format!("{} *{}*\n`{}`", emoji, name_upper, msg),
                                    "parse_mode": "Markdown"
                                }))
                                .send()
                                .await;
                        }
                    }
                    BotEvent::Trade {
                        pair,
                        direction,
                        gross_bps,
                        net_bps,
                        size_usd,
                    } => {
                        trades_count += 1;
                        pnl_bps_total += net_bps;
                        let msg = format!(
                            "💰 TRADE #{} {} {} | gross={:.1}bps net={:.1}bps | ${:.2} | Σ{:.1}bps",
                            trades_count, pair, direction, gross_bps, net_bps, size_usd,
                            pnl_bps_total
                        );
                        info!(
                            event = "trade",
                            bot = %name_upper,
                            %pair, %direction, gross_bps, net_bps, size_usd
                        );
                        if let Ok(mut f) = OpenOptions::new()
                            .create(true).append(true).open(&trades_log)
                        {
                            let _ = writeln!(f, "[{:?}] {}", SystemTime::now(), msg);
                        }
                        if !token.is_empty() && !chat_id.is_empty() {
                            let url = format!(
                                "https://api.telegram.org/bot{}/sendMessage",
                                token
                            );
                            let _ = client
                                .post(url)
                                .json(&serde_json::json!({
                                    "chat_id": chat_id,
                                    "text": format!("{} *{}*\n`{}`", emoji, name_upper, msg),
                                    "parse_mode": "Markdown"
                                }))
                                .send()
                                .await;
                        }
                    }
                    BotEvent::Signal {
                        pair,
                        direction,
                        gross_bps,
                    } => {
                        signals_count += 1;
                        info!(
                            event = "signal",
                            bot = %name_upper,
                            %pair, %direction, gross_bps,
                            total_signals = signals_count
                        );
                    }
                }
            }
        });
        Self { tx }
    }

    /// Send a simple text alert (non-blocking).
    #[inline]
    pub fn alert(&self, msg: String) {
        let _ = self.tx.send(BotEvent::Alert(msg));
    }

    /// Convenience alias for `alert` — used by bots that only send strings.
    #[inline]
    pub fn send(&self, msg: String) {
        self.alert(msg);
    }

    /// Send a structured trade event (non-blocking).
    #[inline]
    pub fn trade(
        &self,
        pair: String,
        direction: String,
        gross_bps: f64,
        net_bps: f64,
        size_usd: f64,
    ) {
        let _ = self.tx.send(BotEvent::Trade {
            pair,
            direction,
            gross_bps,
            net_bps,
            size_usd,
        });
    }

    /// Send a signal event (non-blocking, no TG).
    #[inline]
    pub fn signal(&self, pair: String, direction: String, gross_bps: f64) {
        let _ = self.tx.send(BotEvent::Signal {
            pair,
            direction,
            gross_bps,
        });
    }
}
