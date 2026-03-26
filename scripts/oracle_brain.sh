#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🐺 BEROUN ORACLE v7.1 — Global Macro Intelligence Layer (L3)
# ═══════════════════════════════════════════════════════════════
# Spouští se jednou za 26h přes systemd timer + RandomizedDelaySec
# Využívá Gemini 3.1 Pro (gemini-cli) pro analýzu trendů
# Výstup: úprava risk parametrů přes beroun-config (mmap)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

LOG="/home/wwwenda/hft-sniper/logs/oracle.log"
RUNTIME="/home/wwwenda/hft-sniper/runtime"
CONFIG="/home/wwwenda/hft-sniper/target/release/beroun-config"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "[$TIMESTAMP] $1" | tee -a "$LOG"; }

log "═══════ ORACLE v7.1 ACTIVATED ═══════"

# ── 1. SBĚR LOKÁLNÍCH DAT ────────────────────────────────────
log "[1/5] Collecting bot metrics..."

# Aktuální risk parametry
CURRENT_PARAMS=$($CONFIG show 2>/dev/null || echo "unavailable")

# Posledních 26h obchody a alerty
RECENT_TRADES=$(journalctl --user -u beroun-sniper --since "26h ago" --no-pager 2>/dev/null | \
    grep -o '"event":"trade_executed"[^}]*' | tail -20 || echo "no trades")

PNL_ENTRIES=$(journalctl --user -u beroun-sniper --since "26h ago" --no-pager 2>/dev/null | \
    grep -o '"event":"pnl_realized"[^}]*' | tail -10 || echo "no pnl data")

AI_DECISIONS=$(journalctl -u beroun-ai --since "26h ago" --no-pager 2>/dev/null | \
    grep "AI │" | tail -10 || echo "no ai data")

# ── 2. SBĚR EXTERNÍCH DAT ────────────────────────────────────
log "[2/5] Harvesting market intelligence..."

# RSS feedy - crypto news
COINDESK_RSS=$(curl -sL --max-time 10 "https://www.coindesk.com/arc/outboundfeeds/rss/" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -5 | tr '\n' '; ' || echo "feed unavailable")

COINTELEGRAPH_RSS=$(curl -sL --max-time 10 "https://cointelegraph.com/rss" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -5 | tr '\n' '; ' || echo "feed unavailable")

# Bitfinex announcements
BITFINEX_NEWS=$(curl -sL --max-time 10 "https://www.bitfinex.com/feed" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -3 | tr '\n' '; ' || echo "feed unavailable")

# Bitcoin Fear & Greed (alternativní endpoint)
FEAR_GREED=$(curl -sL --max-time 10 "https://api.alternative.me/fng/?limit=1" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['data'][0]['value']} ({d['data'][0]['value_classification']})\")" 2>/dev/null || echo "unavailable")

log "  Headlines: $(echo "$COINDESK_RSS" | head -c 100)..."
log "  Fear & Greed: $FEAR_GREED"

# ── 3. GEMINI 3.1 PRO ANALÝZA ────────────────────────────────
log "[3/5] Invoking Gemini 3.1 Pro..."

ORACLE_PROMPT="You are the Senior Risk Manager of HFT fund 'Beroun Sniper'.

=== BOT STATUS (last 26h) ===
Current Parameters: $CURRENT_PARAMS
Recent Trades: $RECENT_TRADES
Realized PnL events: $PNL_ENTRIES
Local AI (L2) decisions: $AI_DECISIONS

=== MARKET INTELLIGENCE ===
CoinDesk Headlines: $COINDESK_RSS
CoinTelegraph Headlines: $COINTELEGRAPH_RSS
Bitfinex Announcements: $BITFINEX_NEWS
Fear & Greed Index: $FEAR_GREED

=== YOUR TASK ===
1. Analyze market regime: Is it TRENDING, RANGING, or VOLATILE?
2. Assess if current grid_step is appropriate for the detected regime.
3. Recommend new grid_step (in USD). Current is ~\$5.

Rules:
- VOLATILE/NEWS regime: widen grid (8-15 USD) to avoid adverse selection
- RANGING/CALM regime: tighten grid (3-6 USD) to maximize spread capture
- TRENDING regime: moderate grid (5-10 USD) + note direction

RESPOND WITH EXACTLY THIS JSON FORMAT:
{\"regime\": \"RANGING|TRENDING|VOLATILE\", \"grid_usd\": 5.0, \"reasoning\": \"brief explanation\", \"risk_alert\": false}"

# Volání Gemini - jednorázový prompt (ne interaktivní)
GEMINI_RESPONSE=$(echo "$ORACLE_PROMPT" | gemini -p "$(cat)" --yolo 2>/dev/null | tail -20 || echo '{"regime":"UNKNOWN","grid_usd":5.0,"reasoning":"gemini unavailable","risk_alert":false}')

log "  Gemini raw response: $(echo "$GEMINI_RESPONSE" | head -c 300)"

# ── 4. PARSE A APLIKUJ ───────────────────────────────────────
log "[4/5] Parsing recommendation..."

# Extrakce JSON z Gemini odpovědi
RECOMMENDED_GRID=$(echo "$GEMINI_RESPONSE" | python3 -c "
import sys, json, re
text = sys.stdin.read()
# Najdi JSON v odpovědi
match = re.search(r'\{[^{}]*\"grid_usd\"[^{}]*\}', text)
if match:
    d = json.loads(match.group())
    grid = float(d.get('grid_usd', 5.0))
    # Safety clamp: grid musí být mezi 2 a 50 USD
    grid = max(2.0, min(50.0, grid))
    print(f'{grid:.1f}')
    print(d.get('regime', 'UNKNOWN'), file=sys.stderr)
    print(d.get('reasoning', 'no reason'), file=sys.stderr)
else:
    print('5.0')
" 2>>/tmp/oracle_detail.log || echo "5.0")

REGIME=$(head -1 /tmp/oracle_detail.log 2>/dev/null || echo "UNKNOWN")
REASONING=$(tail -1 /tmp/oracle_detail.log 2>/dev/null || echo "parse error")

log "  Regime: $REGIME"
log "  Recommended grid: \$$RECOMMENDED_GRID"
log "  Reasoning: $REASONING"

# Aplikuj změnu přes beroun-config
if [[ "$RECOMMENDED_GRID" =~ ^[0-9]+\.?[0-9]*$ ]]; then
    $CONFIG set-grid "$RECOMMENDED_GRID" 2>&1 | tee -a "$LOG"
    log "✅ Grid updated to \$$RECOMMENDED_GRID"
else
    log "⚠️  Invalid grid value, keeping current"
fi

# ── 5. TELEGRAM REPORT ────────────────────────────────────────
log "[5/5] Sending Oracle report..."

# Telegram notification (pokud je nastaven)
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    MSG="🔮 *ORACLE v7.1 Report*

📊 Regime: \`$REGIME\`
📐 Grid: \`\$$RECOMMENDED_GRID\`
😱 Fear/Greed: \`$FEAR_GREED\`

💡 $REASONING"

    curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="Markdown" \
        -d text="$MSG" > /dev/null 2>&1 || true
fi

log "═══════ ORACLE COMPLETE ═══════"
