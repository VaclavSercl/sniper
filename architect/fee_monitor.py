"""
💰 Fee Monitor Daemon — Dynamic Exchange Fee Tracking
Sniper Armada · Phase F1 · v19.0

Periodically polls Bitfinex + Binance REST API for current fee tier,
writes results to /dev/shm/beroun/fee_state.bin (GlobalFeeState mmap).

All bots read this mmap to use real-time fees in their calculations.

Run: python3 fee_monitor.py (or via cron every 30 min)
Cron: */30 * * * * cd /home/wwwenda/sniper && python3 architect/fee_monitor.py
"""

import os
import sys
import time
import json
import struct
import mmap
import hmac
import hashlib
import logging
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'architect'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [fee_monitor] %(message)s')
log = logging.getLogger("fee_monitor")

# Load environment
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

FEE_STATE_PATH = "/dev/shm/beroun/fee_state.bin"
FEE_STATE_SIZE = 64  # sizeof(GlobalFeeState) — 1 cache line

# mmap offsets (from fee_types.rs)
OFF_MAKER_BPS = 0       # u64
OFF_TAKER_BPS = 8       # u64
OFF_DERIV_MAKER = 16    # u64
OFF_DERIV_TAKER = 24    # u64
OFF_LAST_UPDATED = 32   # u64
OFF_MONTHLY_VOL = 40    # i64
OFF_FEE_TIER = 48       # u64
OFF_HEARTBEAT = 56      # u64

def _init_mmap():
    """Open or create fee_state.bin mmap."""
    os.makedirs(os.path.dirname(FEE_STATE_PATH), exist_ok=True)
    if not os.path.exists(FEE_STATE_PATH):
        with open(FEE_STATE_PATH, 'wb') as f:
            # Write defaults: maker=1000 (10bps), taker=2000 (20bps)
            f.write(struct.pack('<Q', 1000))   # maker
            f.write(struct.pack('<Q', 2000))   # taker
            f.write(struct.pack('<Q', 200))    # deriv maker
            f.write(struct.pack('<Q', 650))    # deriv taker
            f.write(struct.pack('<Q', 0))      # last_updated
            f.write(struct.pack('<q', 0))      # monthly_volume
            f.write(struct.pack('<Q', 0))      # fee_tier
            f.write(struct.pack('<Q', 0))      # heartbeat
    with open(FEE_STATE_PATH, 'r+b') as f:
        mm = mmap.mmap(f.fileno(), FEE_STATE_SIZE)
    return mm


# ═══════════════════════════════════════════════════════════
# Bitfinex Fee Fetcher
# ═══════════════════════════════════════════════════════════

def fetch_bitfinex_fees():
    """Fetch current fee tier from Bitfinex /v2/auth/r/summary."""
    import urllib.request
    import urllib.error

    key = os.environ.get('BITFINEX_API_KEY', '')
    secret = os.environ.get('BITFINEX_API_SECRET', '')
    if not key or not secret:
        log.warning("Bitfinex API keys not configured, using defaults")
        return None

    nonce = str(int(time.time() * 1000))
    body = '{}'
    path = '/v2/auth/r/summary'
    signature_payload = f'/api{path}{nonce}{body}'
    sig = hmac.new(secret.encode(), signature_payload.encode(), hashlib.sha384).hexdigest()

    url = f'https://api.bitfinex.com{path}'
    req = urllib.request.Request(url, data=body.encode(), method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('bfx-nonce', nonce)
    req.add_header('bfx-apikey', key)
    req.add_header('bfx-signature', sig)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        # Response format: [[maker_fee, ..., maker_rebate], ...]
        # or {fees_funding: ..., fees_trading: {maker_fee: ..., taker_fee: ...}}
        # Handle both array and object format
        if isinstance(data, dict):
            trading = data.get('fees_trading_30d', data.get('fees_trading', {}))
            maker = trading.get('maker_fee', 0.001)  # default 0.1%
            taker = trading.get('taker_fee', 0.002)   # default 0.2%
        elif isinstance(data, list) and len(data) > 0:
            # Array format: [[null, null, null, null, [maker_fee, taker_fee, ...]]]
            fees = data[4] if len(data) > 4 else data[0]
            if isinstance(fees, list) and len(fees) >= 2:
                maker = abs(fees[0])
                taker = abs(fees[1])
            else:
                maker = 0.001
                taker = 0.002
        else:
            maker = 0.001
            taker = 0.002

        # Convert to bps × 100 (e.g., 0.001 = 10 bps = 1000)
        maker_bps100 = int(maker * 1_000_000)
        taker_bps100 = int(taker * 1_000_000)

        log.info(f"Bitfinex fees: maker={maker*100:.3f}% ({maker_bps100}) taker={taker*100:.3f}% ({taker_bps100})")
        return {'maker': maker_bps100, 'taker': taker_bps100}

    except Exception as e:
        log.error(f"Bitfinex fee fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# Binance Fee Fetcher
# ═══════════════════════════════════════════════════════════

def fetch_binance_fees():
    """Fetch current fee from Binance /api/v3/account."""
    import urllib.request

    key = os.environ.get('BINANCE_API_KEY', '')
    secret = os.environ.get('BINANCE_API_SECRET', '')
    if not key or not secret:
        log.warning("Binance API keys not configured, using defaults")
        return None

    timestamp = str(int(time.time() * 1000))
    query = f'timestamp={timestamp}'
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f'https://api.binance.com/api/v3/account?{query}&signature={sig}'

    req = urllib.request.Request(url)
    req.add_header('X-MBX-APIKEY', key)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        maker = data.get('makerCommission', 10) / 10000  # Binance returns basis points / 100
        taker = data.get('takerCommission', 10) / 10000

        # Convert to bps × 100
        maker_bps100 = int(maker * 1_000_000)
        taker_bps100 = int(taker * 1_000_000)

        log.info(f"Binance fees: maker={maker*100:.3f}% ({maker_bps100}) taker={taker*100:.3f}% ({taker_bps100})")
        return {'maker': maker_bps100, 'taker': taker_bps100}

    except Exception as e:
        log.error(f"Binance fee fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# Main: Fetch + Write to mmap
# ═══════════════════════════════════════════════════════════

def update_fees():
    """Fetch fees from both exchanges and write to mmap."""
    mm = _init_mmap()
    now_ms = int(time.time() * 1000)

    # Read previous values for change detection
    prev_maker = struct.unpack_from('<Q', mm, OFF_MAKER_BPS)[0]
    prev_taker = struct.unpack_from('<Q', mm, OFF_TAKER_BPS)[0]

    # Fetch Bitfinex (primary — used by Hydra/Moonshot/Grid/Trigon)
    bfx = fetch_bitfinex_fees()
    if bfx:
        struct.pack_into('<Q', mm, OFF_MAKER_BPS, bfx['maker'])
        struct.pack_into('<Q', mm, OFF_TAKER_BPS, bfx['taker'])
        struct.pack_into('<Q', mm, OFF_LAST_UPDATED, now_ms)

    # Fetch Binance (for Nexus cross-exchange calculations)
    bnb = fetch_binance_fees()
    # Note: We store Binance fees in deriv_maker/taker fields (reusing slots)
    # This is a pragmatic choice — deriv fees are not used currently
    if bnb:
        struct.pack_into('<Q', mm, OFF_DERIV_MAKER, bnb['maker'])
        struct.pack_into('<Q', mm, OFF_DERIV_TAKER, bnb['taker'])

    # Update heartbeat
    struct.pack_into('<Q', mm, OFF_HEARTBEAT, now_ms)

    # Change detection → Telegram alert
    new_maker = struct.unpack_from('<Q', mm, OFF_MAKER_BPS)[0]
    new_taker = struct.unpack_from('<Q', mm, OFF_TAKER_BPS)[0]

    if prev_maker > 0 and (new_maker != prev_maker or new_taker != prev_taker):
        _send_fee_change_alert(prev_maker, prev_taker, new_maker, new_taker)

    mm.close()
    log.info(f"Fee state updated at {datetime.now(timezone.utc).isoformat()}")


def _send_fee_change_alert(old_maker, old_taker, new_maker, new_taker):
    """Send Telegram alert when fees change."""
    try:
        import urllib.request
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        if not token or not chat_id:
            return

        msg = (
            f"💰 *FEE CHANGE DETECTED*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Maker: `{old_maker/100:.1f}` → `{new_maker/100:.1f}` bps\n"
            f"Taker: `{old_taker/100:.1f}` → `{new_taker/100:.1f}` bps\n"
            f"_All bots updated automatically_"
        )

        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}).encode()
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        urllib.request.urlopen(req, timeout=5)
        log.info("Fee change alert sent to Telegram")
    except Exception as e:
        log.error(f"TG alert failed: {e}")


if __name__ == '__main__':
    log.info("💰 Fee Monitor starting...")
    update_fees()
    log.info("✅ Done")
