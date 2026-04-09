import sqlite3
import os
import logging
import time

log = logging.getLogger("zeroclaw_stats")

PRICE_PER_1M_TOKENS_USD = 0.075

def get_zeroclaw_usage() -> dict:
    db_path = "file:///home/wwwenda/.zeroclaw/workspace/memory/brain.db?mode=ro"
    
    result = {
        "daily_tokens": 0,
        "daily_cost": 0.0,
        "monthly_tokens": 0,
        "monthly_cost": 0.0,
        "error": False
    }

    if not os.path.exists("/home/wwwenda/.zeroclaw/workspace/memory/brain.db"):
        return result

    try:
        conn = sqlite3.connect(db_path, uri=True, timeout=1.0)
        cur = conn.cursor()

        cur.execute("""
            SELECT SUM(length(content))/4 
            FROM memories 
            WHERE category='conversation' AND created_at >= datetime('now', '-1 day')
        """)
        row = cur.fetchone()
        if row and row[0]:
            result["daily_tokens"] = int(row[0])
            result["daily_cost"] = round((result["daily_tokens"] / 1_000_000) * PRICE_PER_1M_TOKENS_USD, 4)

        cur.execute("""
            SELECT SUM(length(content))/4 
            FROM memories 
            WHERE category='conversation' AND created_at >= datetime('now', '-30 days')
        """)
        row = cur.fetchone()
        if row and row[0]:
            result["monthly_tokens"] = int(row[0])
            result["monthly_cost"] = round((result["monthly_tokens"] / 1_000_000) * PRICE_PER_1M_TOKENS_USD, 4)

        conn.close()
    except Exception as e:
        log.error(f"Error reading ZeroClaw stats: {e}")
        result["error"] = True

    return result

if __name__ == "__main__":
    print(get_zeroclaw_usage())
