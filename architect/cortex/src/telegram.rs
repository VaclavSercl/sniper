// ═══════════════════════════════════════════════════════════
// 📱 SOVEREIGN CORTEX — Telegram Module
// Sends reports and alerts via Telegram Bot API
//
// v13.1: Robust send with Markdown fallback to plain text
// ═══════════════════════════════════════════════════════════

use std::env;

/// Send a message to the authorized Telegram chat.
/// Tries Markdown first; if Telegram rejects it (bad entities), retries as plain text.
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

    // Try Markdown first
    let url_clone = url.clone();
    let chat_clone = chat_id.clone();
    let text_owned = text.to_string();
    let result = tokio::task::spawn_blocking(move || {
        let client = reqwest::blocking::Client::new();
        client.post(&url_clone)
            .form(&[
                ("chat_id", chat_clone.as_str()),
                ("text", &text_owned),
                ("parse_mode", "Markdown"),
            ])
            .timeout(std::time::Duration::from_secs(10))
            .send()
    })
    .await?;

    match result {
        Ok(resp) => {
            let status = resp.status();
            if status.is_success() {
                return Ok(());
            }

            let body = resp.text().unwrap_or_default();

            // If Markdown parsing failed, retry as plain text
            if body.contains("can't parse entities") || body.contains("Bad Request") {
                eprintln!("  ⚠️ Telegram Markdown failed, retrying as plain text");
                let url_retry = url;
                let chat_retry = chat_id;
                // Strip markdown formatting for plain text fallback
                let plain = text.replace('*', "").replace('_', "").replace('`', "");
                let _ = tokio::task::spawn_blocking(move || {
                    let client = reqwest::blocking::Client::new();
                    client.post(&url_retry)
                        .form(&[
                            ("chat_id", chat_retry.as_str()),
                            ("text", plain.as_str()),
                        ])
                        .timeout(std::time::Duration::from_secs(10))
                        .send()
                })
                .await?;
            } else {
                eprintln!("  ⚠️ Telegram API error ({}): {body}", status.as_u16());
            }
        }
        Err(e) => eprintln!("  ⚠️ Telegram HTTP error: {e}"),
    }

    Ok(())
}
