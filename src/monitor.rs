use feed_rs::parser;
use reqwest;
use std::fs::OpenOptions;
use std::io::Write;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let feeds = vec![
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://blog.bitfinex.com/feed/",
        "https://www.newsbtc.com/feed/",
        "https://cryptoslate.com/feed/"
    ];

    let log_path = "/home/wwwenda/beroun-brain/short_term/news_sentiment.log";
    // Přepisujeme log při každém běhu, aby AI měla jen čerstvé zprávy
    let mut file = OpenOptions::new().create(true).write(true).truncate(true).open(log_path)?;

    println!("--- BEROUN PROACTIVE MONITOR v1.1 ---");

    for url in feeds {
        println!("[FETCH] URL: {}...", url);
        let client = reqwest::Client::builder().timeout(std::time::Duration::from_secs(10)).build()?;
        match client.get(url).send().await {
            Ok(res) => {
                let content = res.bytes().await?;
                if let Ok(feed) = parser::parse(&content[..]) {
                    for entry in feed.entries.iter().take(10) {
                        let title = entry.title.as_ref().map(|t| t.content.clone()).unwrap_or_default();
                        let summary = entry.summary.as_ref().map(|s| s.content.clone()).unwrap_or_default();
                        writeln!(file, "TITLE: {} | SUMMARY: {}", title, summary)?;
                    }
                }
            }
            Err(e) => println!(" - FAIL: {}", e),
        }
    }

    println!(">>> INTELLIGENCE REFRESHED <<<");
    Ok(())
}
