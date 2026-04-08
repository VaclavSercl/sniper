"""
💰 Fee Monitor Daemon — Dynamic Exchange Fee Tracking
Sniper Armada · Phase F1 · v19.0

Periodically polls Bitfinex + Binance REST API for current fee tier,
writes results to /dev/shm/sniper/fee_state.bin (GlobalFeeState mmap).

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
def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

FEE_MATRIX_PATH = "/dev/shm/sniper/fee_matrix.bin"
FEE_MATRIX_SIZE = 512  # MAX_VENUES (8) * 64 bytes

# Constants matching fee_types.rs
VENUE_BITFINEX = 0
VENUE_BINANCE = 1

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
    """Open or create fee_matrix.bin mmap."""
    os.makedirs(os.path.dirname(FEE_MATRIX_PATH), exist_ok=True)
    if not os.path.exists(FEE_MATRIX_PATH):
        with open(FEE_MATRIX_PATH, 'wb') as f:
            f.write(b'\x00' * FEE_MATRIX_SIZE)
            
        # Ochranná brzda - inicializace na bezpečné výchozí hodnoty (20 bps)
        with open(FEE_MATRIX_PATH, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), FEE_MATRIX_SIZE)
            for i in range(8):
                write_venue_fee(mm, i, 2000, 2000, 0, 1) # 20 bps, Online
            mm.close()
            
    with open(FEE_MATRIX_PATH, 'r+b') as f:
        mm = mmap.mmap(f.fileno(), FEE_MATRIX_SIZE)
    return mm

def write_venue_fee(mm, venue_idx, maker_bps100, taker_bps100, last_update_ms, is_online):
    offset = venue_idx * 64
    struct.pack_into('<QQQI', mm, offset, maker_bps100, taker_bps100, last_update_ms, is_online)

def read_venue_fee(mm, venue_idx):
    offset = venue_idx * 64
    maker, taker, ts, is_online = struct.unpack_from('<QQQI', mm, offset)
    return maker, taker


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

    path = '/v2/auth/r/summary'
    for attempt in range(3):
        # Bitfinex vyžaduje striktně rostoucí nonce. Pokud systém dříve vygeneroval
        # nonce v mikrosekundách, milisekundy budou "příliš malé". Použijeme * 1_000_000.
        nonce = str(int(time.time() * 1000000))
        body = '{}'
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
            break  # success
        except Exception as e:
            log.warning(f"Bitfinex attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(5)
            else:
                log.error(f"Bitfinex fee fetch failed after 3 attempts")
                return None

    try:

        # Response format: [null, null, null, null, [[maker_fee,..],[taker_fee,..]],...]
        # or {fees_funding: ..., fees_trading: {maker_fee: ..., taker_fee: ...}}
        # Handle both array and object format
        if isinstance(data, dict):
            trading = data.get('fees_trading_30d', data.get('fees_trading', {}))
            maker = trading.get('maker_fee', 0.001)  # default 0.1%
            taker = trading.get('taker_fee', 0.002)   # default 0.2%
        elif isinstance(data, list) and len(data) > 4:
            # Array format: [null, null, null, null, [[maker,0,0,...],[taker,0,0,...]]]
            fee_pair = data[4]
            if isinstance(fee_pair, list) and len(fee_pair) >= 2:
                maker_arr = fee_pair[0]  # [maker_fee, ...]
                taker_arr = fee_pair[1]  # [taker_fee, ...]
                maker = abs(maker_arr[0]) if isinstance(maker_arr, list) and len(maker_arr) > 0 else 0
                taker = abs(taker_arr[0]) if isinstance(taker_arr, list) and len(taker_arr) > 0 else 0
                log.info(f"Bitfinex raw: maker_arr={maker_arr}, taker_arr={taker_arr}")
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

    # ── Snapshot: read prev values for ALL tracked venues BEFORE any writes ──
    prev_fees = {}
    prev_fees[VENUE_BITFINEX] = read_venue_fee(mm, VENUE_BITFINEX)
    prev_fees[VENUE_BINANCE] = read_venue_fee(mm, VENUE_BINANCE)

    # ── Fetch & Write: Bitfinex (primary — Hydra/Moonshot/Grid/Trigon) ──
    bfx = fetch_bitfinex_fees()
    if bfx:
        write_venue_fee(mm, VENUE_BITFINEX, bfx['maker'], bfx['taker'], now_ms, 1)
    else:
        # Fallback na 20 bps pokud burza neodpovídá a hodnoty dříve byly 0
        prev_maker_bfx = prev_fees[VENUE_BITFINEX][0]
        if prev_maker_bfx == 0:
            log.warning("Bitfinex offline and no previous fee state. Activating 20 bps safety brake!")
            write_venue_fee(mm, VENUE_BITFINEX, 2000, 2000, now_ms, 1)

    # ── Fetch & Write: Binance (for Nexus cross-exchange) ──
    bnb = fetch_binance_fees()
    if bnb:
        write_venue_fee(mm, VENUE_BINANCE, bnb['maker'], bnb['taker'], now_ms, 1)

    # ── Per-venue change detection → Telegram alerts ──
    TRACKED_VENUES = [
        (VENUE_BITFINEX, "Bitfinex"),
        (VENUE_BINANCE, "Binance"),
    ]

    for venue_id, venue_name in TRACKED_VENUES:
        old_m, old_t = prev_fees[venue_id]
        new_m, new_t = read_venue_fee(mm, venue_id)

        if new_m != old_m or new_t != old_t:
            _send_fee_change_alert(venue_id, venue_name, old_m, old_t, new_m, new_t)

    mm.close()
    log.info(f"Fee state updated at {datetime.now(timezone.utc).isoformat()}")


# Seznam burz odpovídající indexům v L2 MMap matici (Venue ID 0 až 7)
VENUE_NAMES = [
    "Bitfinex", "Binance", "Bybit", "OKX",
    "Kraken", "Coinbase", "GateIO", "KuCoin"
]


def _send_fee_change_alert(venue_id, venue_name, old_maker, old_taker, new_maker, new_taker):
    """
    Send per-venue Telegram alert when fees change.
    Values are in bps×100 format (e.g. 2000 = 20 bps = 0.200%).
    """
    try:
        import urllib.request
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        if not token or not chat_id:
            return

        # bps×100 → bps (divide by 100), bps×100 → % (divide by 100_000)
        old_m_bps = old_maker / 100.0
        new_m_bps = new_maker / 100.0
        old_t_bps = old_taker / 100.0
        new_t_bps = new_taker / 100.0
        old_m_pct = old_maker / 100_000.0
        new_m_pct = new_maker / 100_000.0
        old_t_pct = old_taker / 100_000.0
        new_t_pct = new_taker / 100_000.0

        msg = f"💰 *FEE CHANGE: {venue_name}* (ID: {venue_id})\n"
        msg += f"━━━━━━━━━━━━━━━━━\n"
        msg += f"Maker: `{old_m_pct:.3f}%` ({old_m_bps:.0f} bps) → `{new_m_pct:.3f}%` ({new_m_bps:.0f} bps)\n"
        msg += f"Taker: `{old_t_pct:.3f}%` ({old_t_bps:.0f} bps) → `{new_t_pct:.3f}%` ({new_t_bps:.0f} bps)\n"

        if new_maker == 0 and new_taker == 0:
            msg += f"⚠️ L0 Klony na burze {venue_name} přešly na ZERO-FEE režim."
        else:
            msg += f"✅ L0 Klony na burze {venue_name} aktualizovaly asymetrii."

        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}).encode()
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        urllib.request.urlopen(req, timeout=5)
        log.info(f"Fee change alert sent to Telegram for {venue_name}")
    except Exception as e:
        log.error(f"TG alert failed for {venue_name}: {e}")


if __name__ == '__main__':
    log.info("💰 Fee Monitor Daemon starting...")
    while True:
        try:
            update_fees()
        except Exception as e:
            log.error(f"Fee Monitor Loop Error: {e}")
        time.sleep(60)
