"""
🔍 Balance Reconciler — Daily Withdrawal/Deposit Verification
Sniper Armada · Phase 5.5 · v18.0

Queries both exchange REST APIs, compares balances with last known state,
and alerts via Telegram if unexpected changes are detected.

Run: python3 architect/balance_reconciler.py
Cron: 0 6 * * * cd /home/wwwenda/sniper && python3 architect/balance_reconciler.py

Features:
- Reads Bitfinex /v2/auth/r/wallets
- Reads Binance GET /api/v3/account (HMAC-SHA256 signed)
- Compares with last saved snapshot (state/balances.json)
- Alerts if any asset changes by > $10 unexpectedly
- Logs all results to balance_reconciler.log
"""

import os
import sys
import json
import time
import hmac
import hashlib
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ── Config ──
PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
STATE_DIR = os.path.join(PROJECT_ROOT, 'state')
BALANCE_FILE = os.path.join(STATE_DIR, 'balances.json')
LOG_FILE = os.path.join(PROJECT_ROOT, 'logs', 'balance_reconciler.log')
ALERT_THRESHOLD_USD = 10.0  # Alert if balance changes by > $10

# Ensure directories exist
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, mode='a'),
    ]
)
log = logging.getLogger("reconciler")

# ── Load .env ──
def load_env():
    env_path = os.path.join(PROJECT_ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))

load_env()


# ═══════════════════════════════════════════════════════════
# Binance Balance Reader
# ═══════════════════════════════════════════════════════════

def read_binance_balances():
    """Query Binance GET /api/v3/account with HMAC-SHA256."""
    key = os.environ.get('BINANCE_API_KEY', '')
    secret = os.environ.get('BINANCE_API_SECRET', '')
    if not key or not secret:
        log.warning("Binance credentials not found in .env")
        return {}

    ts = str(int(time.time() * 1000))
    params = f'timestamp={ts}'
    sig = hmac.new(secret.encode(), params.encode(), hashlib.sha256).hexdigest()
    url = f'https://api.binance.com/api/v3/account?{params}&signature={sig}'

    req = urllib.request.Request(url, headers={'X-MBX-APIKEY': key})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        balances = {}
        for b in data.get('balances', []):
            free = float(b['free'])
            locked = float(b['locked'])
            total = free + locked
            if total > 0:
                balances[b['asset']] = {
                    'free': free,
                    'locked': locked,
                    'total': total,
                }
        return balances
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Binance API error {e.code}: {body}")
        return {}
    except Exception as e:
        log.error(f"Binance connection error: {e}")
        return {}


# ═══════════════════════════════════════════════════════════
# Bitfinex Balance Reader
# ═══════════════════════════════════════════════════════════

def read_bitfinex_wallets():
    """Query Bitfinex POST /v2/auth/r/wallets with HMAC-SHA384."""
    key = os.environ.get('BITFINEX_API_KEY', '')
    secret = os.environ.get('BITFINEX_API_SECRET', '')
    if not key or not secret:
        log.warning("Bitfinex credentials not found in .env")
        return {}

    nonce = str(int(time.time() * 1000))
    path = '/v2/auth/r/wallets'
    body = '{}'
    sig_payload = f'/api{path}{nonce}{body}'
    sig = hmac.new(
        secret.encode(), sig_payload.encode(), hashlib.sha384
    ).hexdigest()

    headers = {
        'bfx-apikey': key,
        'bfx-apisecret': secret,
        'bfx-nonce': nonce,
        'bfx-signature': sig,
        'Content-Type': 'application/json',
    }

    url = f'https://api.bitfinex.com{path}'
    req = urllib.request.Request(url, data=body.encode(), headers=headers, method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        # Bitfinex returns: [[WALLET_TYPE, CURRENCY, BALANCE, ...], ...]
        wallets = {}
        for w in data:
            if len(w) >= 3 and w[2] and float(w[2]) > 0:
                wallet_type = w[0]  # "exchange", "margin", "funding"
                currency = w[1]
                balance = float(w[2])
                available = float(w[4]) if len(w) > 4 else balance
                wallet_key = f"{wallet_type}:{currency}"
                wallets[wallet_key] = {
                    'type': wallet_type,
                    'currency': currency,
                    'balance': balance,
                    'available': available,
                }
        return wallets
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Bitfinex API error {e.code}: {body}")
        return {}
    except Exception as e:
        log.error(f"Bitfinex connection error: {e}")
        return {}


# ═══════════════════════════════════════════════════════════
# Reconciliation Logic
# ═══════════════════════════════════════════════════════════

def load_previous():
    """Load last saved balance snapshot."""
    try:
        with open(BALANCE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_snapshot(snapshot):
    """Save current balance snapshot."""
    with open(BALANCE_FILE, 'w') as f:
        json.dump(snapshot, f, indent=2)


def reconcile():
    """Main reconciliation: query both exchanges, compare, alert."""
    now = datetime.now(timezone(timedelta(hours=1))).strftime("%Y-%m-%d %H:%M CET")
    log.info(f"═══ Balance Reconciliation — {now} ═══")

    # Read current balances
    log.info("Reading Binance balances...")
    bnb = read_binance_balances()
    log.info(f"  Binance: {len(bnb)} assets with balance")

    log.info("Reading Bitfinex wallets...")
    bfx = read_bitfinex_wallets()
    log.info(f"  Bitfinex: {len(bfx)} wallets with balance")

    # Build snapshot
    current = {
        'timestamp': now,
        'binance': bnb,
        'bitfinex': bfx,
    }

    # Compare with previous
    previous = load_previous()
    alerts = []

    if previous:
        # Binance comparison
        prev_bnb = previous.get('binance', {})
        for asset, cur in bnb.items():
            prev = prev_bnb.get(asset, {})
            prev_total = prev.get('total', 0)
            delta = cur['total'] - prev_total
            if abs(delta) > 0.00001:
                msg = f"BNB {asset}: {prev_total:.6f} → {cur['total']:.6f} (Δ {delta:+.6f})"
                log.info(f"  📊 {msg}")
                if abs(delta) > ALERT_THRESHOLD_USD:
                    alerts.append(msg)

        # Check for disappeared assets
        for asset in prev_bnb:
            if asset not in bnb:
                msg = f"BNB {asset}: {prev_bnb[asset]['total']:.6f} → 0 (GONE!)"
                log.warning(f"  ⚠️ {msg}")
                alerts.append(msg)

        # Bitfinex comparison
        prev_bfx = previous.get('bitfinex', {})
        for wkey, cur in bfx.items():
            prev = prev_bfx.get(wkey, {})
            prev_bal = prev.get('balance', 0)
            delta = cur['balance'] - prev_bal
            if abs(delta) > 0.00001:
                msg = f"BFX {wkey}: {prev_bal:.6f} → {cur['balance']:.6f} (Δ {delta:+.6f})"
                log.info(f"  📊 {msg}")
                if abs(delta) > ALERT_THRESHOLD_USD:
                    alerts.append(msg)

        for wkey in prev_bfx:
            if wkey not in bfx:
                msg = f"BFX {wkey}: {prev_bfx[wkey]['balance']:.6f} → 0 (GONE!)"
                log.warning(f"  ⚠️ {msg}")
                alerts.append(msg)
    else:
        log.info("  No previous snapshot — this is the first run. Saving baseline.")

    # Save snapshot
    save_snapshot(current)
    log.info(f"  Snapshot saved to {BALANCE_FILE}")

    # Summary
    bnb_summary = ", ".join(f"{a}={v['total']:.4f}" for a, v in sorted(bnb.items())[:5])
    bfx_summary = ", ".join(f"{v['currency']}={v['balance']:.4f}" for v in sorted(bfx.values(), key=lambda x: -x['balance'])[:5])
    log.info(f"  BNB top: {bnb_summary}")
    log.info(f"  BFX top: {bfx_summary}")

    # Telegram alert
    if alerts:
        _send_telegram_alert(alerts, now)
    else:
        log.info("  ✅ No unexpected balance changes")

    return {
        'alerts': alerts,
        'binance_assets': len(bnb),
        'bitfinex_wallets': len(bfx),
    }


def _send_telegram_alert(alerts, timestamp):
    """Send balance change alerts to Telegram."""
    token = os.environ.get('TELEGRAM_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        log.warning("Telegram credentials not configured")
        return

    lines = [f"🔍 *BALANCE RECONCILIATION*", f"_{timestamp}_", "━━━━━━━━━━━━━━━━━━━"]
    for a in alerts:
        lines.append(f"⚠️ {a}")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"_{len(alerts)} change(s) > ${ALERT_THRESHOLD_USD:.0f}_")

    msg = "\n".join(lines)

    try:
        data = json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data=data,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=10)
        log.info(f"  📱 Telegram alert sent ({len(alerts)} changes)")
    except Exception as e:
        log.error(f"  Telegram send failed: {e}")


if __name__ == '__main__':
    result = reconcile()
    if result['alerts']:
        log.warning(f"⚠️ {len(result['alerts'])} alerts!")
    else:
        log.info("✅ All balances OK")
