#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🐺 BEROUN ORACLE v7.1 — Global Macro Intelligence Layer (L3)
# ═══════════════════════════════════════════════════════════════
# Cycle: 26h + RandomizedDelaySec (systemd timer)
# Engine: Gemini 3.1 Pro (gemini-cli v0.35.0)
# Output: grid_step + max_inv_delta via beroun-config (mmap)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

LOG="/home/wwwenda/hft-sniper/logs/oracle.log"
CONFIG="/home/wwwenda/hft-sniper/target/release/beroun-config"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p /home/wwwenda/hft-sniper/logs

log() { echo "[$TIMESTAMP] $1" | tee -a "$LOG"; }

log "═══════ ORACLE v7.1 ACTIVATED ═══════"

# ── 1. LOKÁLNÍ DATA ──────────────────────────────────────────
log "[1/5] Collecting bot state..."

BOT_STATE=$($CONFIG export-json 2>/dev/null || echo '{"error":"unavailable"}')
CURRENT_PARAMS=$($CONFIG show 2>/dev/null || echo "unavailable")

RECENT_TRADES=$(journalctl --user -u beroun-sniper --since "26h ago" --no-pager 2>/dev/null | \
    grep -oP '"event":"trade_executed"[^}]*' | wc -l || echo "0")

PNL_EVENTS=$(journalctl --user -u beroun-sniper --since "26h ago" --no-pager 2>/dev/null | \
    grep -oP '"event":"pnl_realized","gain":[-0-9.]+' | \
    awk -F: '{s+=$NF} END {printf "%.4f", s}' || echo "0")

AI_SAMPLES=$(journalctl -u beroun-ai --since "26h ago" --no-pager 2>/dev/null | \
    grep "AI │" | tail -5 || echo "no ai data")

log "  Trades (26h): $RECENT_TRADES | PnL sum: $PNL_EVENTS"

# ── 2. EXTERNÍ DATA ──────────────────────────────────────────
log "[2/5] Harvesting market intelligence..."

COINDESK=$(curl -sL --max-time 10 "https://www.coindesk.com/arc/outboundfeeds/rss/" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -5 | tr '\n' '; ' || echo "unavailable")

COINTELEGRAPH=$(curl -sL --max-time 10 "https://cointelegraph.com/rss" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -5 | tr '\n' '; ' || echo "unavailable")

FEAR_GREED=$(curl -sL --max-time 10 "https://api.alternative.me/fng/?limit=1" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['data'][0]['value']} ({d['data'][0]['value_classification']})\")" 2>/dev/null || echo "unavailable")

log "  Fear & Greed: $FEAR_GREED"
log "  Headlines: $(echo "$COINDESK" | head -c 80)..."

# ── 3. GEMINI 3.1 PRO ────────────────────────────────────────
log "[3/5] Invoking Gemini 3.1 Pro..."

ORACLE_PROMPT="You are the Strategic Oracle for HFT system 'Beroun Sniper'.
Your goal: SURVIVAL and long-term profitability.

=== BOT STATE (live mmap snapshot) ===
$BOT_STATE

=== TRADING PERFORMANCE (26h) ===
Total Trades: $RECENT_TRADES
Net Realized PnL: \$$PNL_EVENTS
Local AI (L1) last samples: $AI_SAMPLES
Current Config: $CURRENT_PARAMS

=== MARKET INTELLIGENCE ===
CoinDesk: $COINDESK
CoinTelegraph: $COINTELEGRAPH
Fear & Greed Index: $FEAR_GREED

=== ANALYSIS REQUIRED ===
1. REGIME: Is BTC/USD TRENDING, RANGING, or VOLATILE right now?
2. GRID: Is current grid_step optimal? Consider:
   - VOLATILE/NEWS: widen to 8-15 USD (avoid adverse selection)
   - RANGING/CALM: tighten to 3-6 USD (maximize spread capture)
   - TRENDING: moderate 5-10 USD
3. INVENTORY RISK: Should max position be adjusted?
4. ADVERSE SELECTION: Are we losing on large moves? (check PnL vs trade count)

RESPOND WITH EXACTLY THIS JSON:
{\"new_grid\": 5.0, \"max_position\": 0.005, \"risk_level\": \"low\", \"reasoning\": \"brief explanation\"}"

# Gemini call — pipe prompt, non-interactive
GEMINI_RAW=$(echo "$ORACLE_PROMPT" | timeout 120 gemini -p "$(cat)" 2>/dev/null || echo '{"new_grid":5.0,"max_position":0.005,"risk_level":"unknown","reasoning":"gemini timeout"}')

log "  Gemini response: $(echo "$GEMINI_RAW" | head -c 300)"

# ── 4. PARSE & APPLY ─────────────────────────────────────────
log "[4/5] Parsing and applying..."

# Extract JSON from response, apply safety
RESULT=$(echo "$GEMINI_RAW" | python3 -c "
import sys, json, re

text = sys.stdin.read()
match = re.search(r'\{[^{}]*\"new_grid\"[^{}]*\}', text)

if match:
    d = json.loads(match.group())
    grid = max(2.0, min(50.0, float(d.get('new_grid', 5.0))))
    pos = max(0.001, min(0.05, float(d.get('max_position', 0.005))))
    risk = d.get('risk_level', 'unknown')
    reason = d.get('reasoning', 'no reason')[:200]
    print(json.dumps({'grid': grid, 'pos': pos, 'risk': risk, 'reason': reason}))
else:
    print(json.dumps({'grid': 5.0, 'pos': 0.005, 'risk': 'parse_error', 'reason': 'no json found'}))
" 2>/dev/null || echo '{"grid":5.0,"pos":0.005,"risk":"error","reason":"parse failed"}')

NEW_GRID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['grid'])")
NEW_POS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['pos'])")
RISK=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['risk'])")
REASON=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")

log "  Regime: $RISK | Grid: \$$NEW_GRID | MaxPos: $NEW_POS BTC"
log "  Reasoning: $REASON"

# Apply via beroun-config (with built-in sanity checks)
$CONFIG set-grid "$NEW_GRID" 2>&1 | tee -a "$LOG"
$CONFIG set-max-inv "$NEW_POS" 2>&1 | tee -a "$LOG"

# ── 5. TELEGRAM REPORT ───────────────────────────────────────
log "[5/5] Sending report..."

if [ -f /home/wwwenda/hft-sniper/.env ]; then
    source /home/wwwenda/hft-sniper/.env
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    MSG="🔮 *ORACLE v7.1*

📊 Regime: \`$RISK\`
📐 Grid: \`\$$NEW_GRID\`
📦 MaxPos: \`$NEW_POS BTC\`
😱 Fear/Greed: \`$FEAR_GREED\`
📈 Trades (26h): \`$RECENT_TRADES\`
💰 PnL (26h): \`\$$PNL_EVENTS\`

💡 _${REASON}_"

    curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="Markdown" \
        -d text="$MSG" > /dev/null 2>&1 || true
fi

log "═══════ ORACLE COMPLETE ═══════"
