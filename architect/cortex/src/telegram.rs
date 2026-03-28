// ═══════════════════════════════════════════════════════════
// 📱 SOVEREIGN CORTEX — Telegram Module (v14.0)
// Slim version: uses ureq (already in deps) instead of reqwest.
// Only used by Sentinel for critical alerts.
// ═══════════════════════════════════════════════════════════

use std::env;

/// Send a message to the authorized Telegram chat.
/// Uses ureq (lightweight, already in deps for GPU module).
pub async fn send(text: &str) -> anyhow::Result<()> {
    let token = env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default();
    let chat_id = env::var("TELEGRAM_CHAT_ID").unwrap_or_default();

    if token.is_empty() || chat_id.is_empty() {
        eprintln!("  ⚠️ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set");
        return Ok(());
    }

    let url = format!("https://api.telegram.org/bot{token}/sendMessage");
    // Strip markdown chars to avoid parse failures
    let clean = text.replace('*', "").replace('_', "").replace('`', "");

    let url_clone = url.clone();
    let chat_clone = chat_id.clone();
    let text_clone = clean.clone();

    tokio::task::spawn_blocking(move || {
        let agent = ureq::Agent::new_with_config(
            ureq::config::Config::builder()
                .timeout_global(Some(std::time::Duration::from_secs(10)))
                .build(),
        );
        let result = agent
            .post(&url_clone)
            .send_form([("chat_id", chat_clone.as_str()), ("text", text_clone.as_str())]);
        if let Err(e) = result {
            eprintln!("  ⚠️ Telegram send error: {e}");
        }
    })
    .await?;

    Ok(())
}
