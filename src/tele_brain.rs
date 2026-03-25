use std::env;
use std::time::Duration;
use tokio::time::sleep;
use serde_json::{json, Value};
use std::process::Command;
use std::fs;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenvy::dotenv().ok();
    
    // SINGLETON CHECK: Prevence duplicitních procesů
    let pid_file = "/tmp/beroun_tele_brain.pid";
    if let Ok(old_pid) = fs::read_to_string(pid_file) {
        if let Ok(pid) = old_pid.trim().parse::<i32>() {
            // Kontrola, zda proces s tímto PID skutečně běží
            if Command::new("ps").arg("-p").arg(pid.to_string()).output().map(|o| o.status.success()).unwrap_or(false) {
                println!("⚠️ Instance už běží (PID {}). Ukončuji se.", pid);
                return Ok(());
            }
        }
    }
    fs::write(pid_file, std::process::id().to_string())?;

    let token = env::var("TELEGRAM_BOT_TOKEN").expect("Chybí TELEGRAM_BOT_TOKEN");
    let allowed_chat_id: i64 = env::var("TELEGRAM_CHAT_ID").expect("Chybí TELEGRAM_CHAT_ID").parse()?;

    println!("🐺 --- BEROUN TELE-BRAIN v1.6 (Singleton) ONLINE ---");

    let client = reqwest::Client::builder().timeout(Duration::from_secs(30)).build()?;
    
    // Inicializace offsetu (přeskočit staré zprávy)
    let mut offset = 0;
    let res = client.get(format!("https://api.telegram.org/bot{}/getUpdates", token)).query(&[("offset", "-1")]).send().await?;
    if let Ok(json) = res.json::<Value>().await {
        if let Some(updates) = json["result"].as_array() {
            if let Some(last) = updates.last() {
                offset = last["update_id"].as_i64().unwrap_or(0) + 1;
            }
        }
    }

    loop {
        let url = format!("https://api.telegram.org/bot{}/getUpdates", token);
        let res = match client.get(&url).query(&[("offset", offset.to_string()), ("timeout", "20".to_string())]).send().await {
            Ok(r) => r,
            Err(_) => { sleep(Duration::from_secs(5)).await; continue; }
        };

        if let Ok(json_res) = res.json::<Value>().await {
            if let Some(updates) = json_res["result"].as_array() {
                for update in updates {
                    offset = update["update_id"].as_i64().unwrap_or(0) + 1;

                    if let Some(msg) = update.get("message") {
                        let chat_id = msg["chat"]["id"].as_i64().unwrap_or(0);
                        if chat_id != allowed_chat_id { continue; }

                        if let Some(text) = msg["text"].as_str() {
                            println!("📥 Zpráva: {}", text);
                            
                            let ack_url = format!("https://api.telegram.org/bot{}/sendMessage", token);
                            let _ = client.post(&ack_url).json(&json!({"chat_id": chat_id, "text": "⏳ _Beroun analyzuje požadavek..._", "parse_mode": "Markdown"})).send().await;

                            // VOLÁNÍ AI (YOLO MODE)
                            let prompt = format!(
                                "UŽIVATEL: '{}'. \
                                Jsi Beroun AI (v4.9.11). Odpověz krátce a profesionálně. \
                                Pokud se ptá na report, zkontroluj stav v /home/wwwenda/beroun-brain/short_term/.", text
                            );

                            let output = Command::new("/usr/local/bin/gemini")
                                .env("HOME", "/home/wwwenda")
                                .arg("-p").arg(&prompt)
                                .arg("--yolo")
                                .current_dir("/home/wwwenda/beroun-projects/beroun-core")
                                .output();

                            let reply = match output {
                                Ok(out) => {
                                    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
                                    let cleaned: String = stdout.lines()
                                        .filter(|l| !l.contains("YOLO") && !l.contains("Keychain") && !l.contains("Loaded"))
                                        .collect::<Vec<_>>()
                                        .join("\n");
                                    if cleaned.trim().is_empty() { "✅ Úkol zpracován.".to_string() } else { cleaned }
                                }
                                Err(e) => format!("❌ AI Error: {}", e),
                            };

                            let _ = client.post(&ack_url).json(&json!({"chat_id": chat_id, "text": reply.trim()})).send().await;
                        }
                    }
                }
            }
        }
        sleep(Duration::from_millis(500)).await;
    }
}
