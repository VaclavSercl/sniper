// ═══════════════════════════════════════════════════════════
// 📱 SOVEREIGN CORTEX — Telegram Module
// Sends reports and alerts via Telegram Bot API
// ═══════════════════════════════════════════════════════════

use std::env;

/// Send a Markdown-formatted message to the authorized Telegram chat.
pub async fn send(text: &str) -> anyhow::Result<()> {
    let token = env::var("TELEGRAM_BOT_TOKEN")
        .unwrap_or_default();
    let chat_id = env::var("TELEGRAM_CHAT_ID")
        .unwrap_or_default();

    if token.is_empty() || chat_id.is_empty() {
        eprintln!("  ⚠️ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set");
        return Ok(());
    }

    let url = format!(
        "https://api.telegram.org/bot{token}/sendMessage"
    );

    // Use a simple blocking HTTP POST (Telegram API is not latency-critical)
    let text_owned = text.to_string();
    let result = tokio::task::spawn_blocking(move || {
        let client = reqwest::blocking::Client::new();
        client.post(&url)
            .form(&[
                ("chat_id", chat_id.as_str()),
                ("text", &text_owned),
                ("parse_mode", "Markdown"),
            ])
            .timeout(std::time::Duration::from_secs(10))
            .send()
    })
    .await?;

    match result {
        Ok(resp) => {
            if !resp.status().is_success() {
                let body = resp.text().unwrap_or_default();
                eprintln!("  ⚠️ Telegram API error: {body}");
            }
        }
        Err(e) => eprintln!("  ⚠️ Telegram HTTP error: {e}"),
    }

    Ok(())
}
