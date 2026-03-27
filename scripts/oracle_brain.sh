#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🐺 BEROUN ORACLE v9.2 — Symbiotic Intelligence Layer (L2)
# ═══════════════════════════════════════════════════════════════
# Architecture: Hybrid Intelligence Framework (March 2026)
#   L0 = Rust Sniper (µs execution)
#   L1 = GPU AI Shield (OBI skew, sweep protection, via mmap)
#   L2 = THIS — Strategic Architect (Gemini 3.1 Pro)
# ═══════════════════════════════════════════════════════════════
# Cycle: 1h (systemd timer) + on-demand via /oracle command
# Engine: Gemini 3.1 Pro
# Data Bridge: beroun-config export-json → analytics section
# Output: grid_step + max_inv_delta + risk_bias via mmap
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

LOG="/home/wwwenda/hft-sniper/logs/oracle.log"
CONFIG="/home/wwwenda/hft-sniper/target/release/beroun-config"
ANALYTICS_SCRIPT="/home/wwwenda/hft-sniper/scripts/analytics.py"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p /home/wwwenda/hft-sniper/logs

log() { echo "[$TIMESTAMP] $1" | tee -a "$LOG"; }

log "═══════ ORACLE v9.2 SYMBIOTIC ACTIVATED ═══════"

# ── 1. LOKÁLNÍ DATA (mmap snapshot + analytics) ──────────────
log "[1/6] Collecting bot state + analytics..."

BOT_STATE=$($CONFIG export-json 2>/dev/null || echo '{"error":"unavailable"}')
CURRENT_PARAMS=$($CONFIG show 2>/dev/null || echo "unavailable")

# Extract key analytics from export-json
SPREAD_CAPTURE=$(echo "$BOT_STATE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    a = d.get('analytics', {})
    print(f\"Fills: {a.get('session_fills', 0)}\")
    print(f\"Buy Vol: {a.get('session_buy_volume_btc', 0):.5f} BTC\")
    print(f\"Sell Vol: {a.get('session_sell_volume_btc', 0):.5f} BTC\")
    print(f\"Spread Capture: \${a.get('net_spread_capture_per_btc', 0):.2f}/BTC\")
    print(f\"Toxic Hits: {a.get('toxic_flow_hits', 0)}\")
    l1 = d.get('l1_intelligence', {})
    print(f\"L1 Skew: \${l1.get('l1_skew_adjustment_usd', 0):.2f}\")
except: print('analytics unavailable')
" 2>/dev/null || echo "analytics unavailable")

log "  $SPREAD_CAPTURE"

# ── 2. TRADE LOG ANALYTICS (Python deep analysis) ───────────
log "[2/6] Running deep trade analytics..."

TRADE_ANALYTICS=$(python3 "$ANALYTICS_SCRIPT" --json 2>/dev/null || echo '{"error":"no data"}')

log "  Analytics: $(echo "$TRADE_ANALYTICS" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(f\"PnL:\${d.get('total_pnl',0):.4f} WR:{d.get('win_rate',0)}% Sharpe:{d.get('sharpe_ratio',0)}\")
except: print('parse err')
" 2>/dev/null)"

# ── 3. EXTERNÍ DATA ──────────────────────────────────────────
log "[3/6] Harvesting market intelligence..."

COINDESK=$(curl -sL --max-time 10 "https://www.coindesk.com/arc/outboundfeeds/rss/" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -5 | tr '\n' '; ' || echo "unavailable")

COINTELEGRAPH=$(curl -sL --max-time 10 "https://cointelegraph.com/rss" 2>/dev/null | \
    grep -oP '(?<=<title>).*?(?=</title>)' | head -5 | tr '\n' '; ' || echo "unavailable")

FEAR_GREED=$(curl -sL --max-time 10 "https://api.alternative.me/fng/?limit=1" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['data'][0]['value']} ({d['data'][0]['value_classification']})\")" 2>/dev/null || echo "unavailable")

log "  Fear & Greed: $FEAR_GREED"

# ── 4. SPREAD CAPTURE AUDIT (L2 Auto-Tune) ──────────────────
log "[4/6] Running Spread Capture Audit..."

# This is the key L2 logic: autonomous parameter adjustment
AUTO_ADJUST=$(echo "$BOT_STATE" | python3 -c "
import sys, json

try:
    d = json.load(sys.stdin)
    a = d.get('analytics', {})
    rp = d.get('risk_params', {})
    
    current_grid = rp.get('grid_step_usd', 5.0)
    fills = a.get('session_fills', 0)
    spread = a.get('net_spread_capture_per_btc', 0)
    toxic = a.get('toxic_flow_hits', 0)
    
    # === SPREAD CAPTURE AUDIT ===
    adjustments = []
    new_grid = current_grid
    
    # Rule 1: Negative or very low spread → WIDEN grid
    if spread < 1.0 and fills > 50:
        new_grid = max(current_grid * 1.5, 5.0)
        adjustments.append(f'LOW_SPREAD: {spread:.2f}/BTC → widen grid {current_grid:.1f}→{new_grid:.1f}')
    
    # Rule 2: Spread > 10 USD with low fills → TIGHTEN grid
    elif spread > 10.0 and fills < 20:
        new_grid = max(current_grid * 0.75, 3.0)
        adjustments.append(f'WIDE_SPREAD: {spread:.2f}/BTC, low fills → tighten {current_grid:.1f}→{new_grid:.1f}')
    
    # Rule 3: High toxic flow → WIDEN + increase max_inv
    if toxic > fills * 0.2 and fills > 20:
        new_grid = max(new_grid * 1.3, 8.0)
        adjustments.append(f'TOXIC_FLOW: {toxic} hits ({toxic/max(fills,1)*100:.0f}%) → widen to {new_grid:.1f}')
    
    # Rule 4: Very high fills with good spread → optimal, slight tighten
    if fills > 100 and spread > 3.0 and spread < 8.0:
        adjustments.append(f'OPTIMAL: {fills} fills, spread {spread:.2f} ✅')
    
    # Clamp
    new_grid = max(2.0, min(25.0, new_grid))
    
    result = {
        'auto_grid': round(new_grid, 1),
        'adjustments': adjustments,
        'spread': round(spread, 2),
        'fills': fills,
        'toxic': toxic
    }
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({'auto_grid': 5.0, 'adjustments': [f'error: {e}'], 'spread': 0, 'fills': 0, 'toxic': 0}))
" 2>/dev/null || echo '{"auto_grid":5.0,"adjustments":["parse_error"]}')

AUTO_GRID=$(echo "$AUTO_ADJUST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('auto_grid', 5.0))")
AUDIT_DETAIL=$(echo "$AUTO_ADJUST" | python3 -c "import sys,json; print('; '.join(json.load(sys.stdin).get('adjustments', ['none'])))")

log "  Spread Audit: $AUDIT_DETAIL"
log "  Auto-Grid suggestion: \$$AUTO_GRID"

# ── 5. GEMINI 3.1 PRO (Strategic Override) ───────────────────
log "[5/6] Invoking Gemini 3.1 Pro..."

ORACLE_PROMPT="You are L2 Strategic Oracle for 'Beroun Sniper' HFT bot.
Architecture: L0 (Rust µs execution) → L1 (GPU OBI/Sweep shield) → L2 (YOU).

=== BOT STATE (live mmap snapshot) ===
$BOT_STATE

=== TRADE ANALYTICS (session) ===
$SPREAD_CAPTURE

=== DEEP ANALYTICS (log-based) ===
$TRADE_ANALYTICS

=== L2 SPREAD CAPTURE AUDIT (auto) ===
$AUDIT_DETAIL
Auto-suggested grid: \$$AUTO_GRID

=== MARKET INTELLIGENCE ===
CoinDesk: $COINDESK
CoinTelegraph: $COINTELEGRAPH
Fear & Greed Index: $FEAR_GREED

=== YOUR MISSION ===
1. VALIDATE the auto-suggested grid (\$$AUTO_GRID). Override ONLY if you have strong macro reason.
2. REGIME: Is BTC/USD TRENDING, RANGING, or VOLATILE?
3. RISK: Should max_position be adjusted? (current: from bot state)
4. CRITICAL: If net_spread_capture < 0, this is an EMERGENCY — recommend aggressive widening.
5. L1 QUALITY: Are toxic_flow_hits reasonable? Should we adjust L1 sensitivity?

RESPOND WITH EXACTLY THIS JSON:
{\"new_grid\": 5.0, \"max_position\": 0.005, \"risk_level\": \"low\", \"reasoning\": \"brief explanation\", \"l1_advice\": \"keep/increase/decrease sensitivity\"}"

# Gemini call
GEMINI_RAW=$(echo "$ORACLE_PROMPT" | timeout 120 gemini -p "$(cat)" 2>/dev/null || echo '{"new_grid":'$AUTO_GRID',"max_position":0.005,"risk_level":"unknown","reasoning":"gemini timeout, using auto-tune","l1_advice":"keep"}')

log "  Gemini response: $(echo "$GEMINI_RAW" | head -c 300)"

# ── 6. PARSE, APPLY & REPORT ─────────────────────────────────
log "[6/6] Parsing and applying..."

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
    l1 = d.get('l1_advice', 'keep')
    print(json.dumps({'grid': grid, 'pos': pos, 'risk': risk, 'reason': reason, 'l1': l1}))
else:
    print(json.dumps({'grid': $AUTO_GRID, 'pos': 0.005, 'risk': 'parse_error', 'reason': 'no json found, using auto-tune', 'l1': 'keep'}))
" 2>/dev/null || echo '{"grid":'$AUTO_GRID',"pos":0.005,"risk":"error","reason":"parse failed, using auto-tune","l1":"keep"}')

NEW_GRID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['grid'])")
NEW_POS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['pos'])")
RISK=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['risk'])")
REASON=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")
L1_ADVICE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['l1'])")

log "  Regime: $RISK | Grid: \$$NEW_GRID | MaxPos: $NEW_POS BTC | L1: $L1_ADVICE"
log "  Reasoning: $REASON"

# Apply via beroun-config (with built-in sanity checks)
$CONFIG set-grid "$NEW_GRID" 2>&1 | tee -a "$LOG"
$CONFIG set-max-inv "$NEW_POS" 2>&1 | tee -a "$LOG"

# Telegram Report
if [ -f /home/wwwenda/hft-sniper/.env ]; then
    source /home/wwwenda/hft-sniper/.env
fi

SPREAD_VAL=$(echo "$AUTO_ADJUST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('spread', 0))")
FILLS_VAL=$(echo "$AUTO_ADJUST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('fills', 0))")
TOXIC_VAL=$(echo "$AUTO_ADJUST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('toxic', 0))")

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    MSG="🔮 *ORACLE v9.2 SYMBIOTIC*

🏗️ Architecture: L0→L1→L2

📊 *Session Analytics:*
  Fills: \`$FILLS_VAL\`
  Spread: \`\$$SPREAD_VAL/BTC\`
  Toxic: \`$TOXIC_VAL hits\`

⚡ *Decisions:*
  Regime: \`$RISK\`
  Grid: \`\$$NEW_GRID\`
  MaxPos: \`$NEW_POS BTC\`
  L1 Advice: \`$L1_ADVICE\`

😱 Fear/Greed: \`$FEAR_GREED\`

💡 _${REASON}_"

    curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="Markdown" \
        -d text="$MSG" > /dev/null 2>&1 || true
fi

log "═══════ ORACLE v9.2 COMPLETE ═══════"
