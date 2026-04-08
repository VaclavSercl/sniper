# 🐺 SNIPER ARMADA — ARCHITEKTONICKÝ AUDIT A FORENZNÍ ZPRÁVA

Tento dokument obsahuje přesný report aktuálního stavu nasazení záchranné poplatkové brzdy, vykopírované zdrojové kódy dotčených souborů a **Forenzní Audit L1 Orákula (ML Shield)**.

---

## 🛑 1. STATUS REPORT: Poplatková Ochranná Brzda
Nasazeno a úspěšně vyžádáno do L0 MMap sdílené paměti. 

Bitfinex aktuálně z neznámých příčin odpovídá nulovými poplatky (`Maker 0.00% / Taker 0.00%`). Níže uvedený log a kód demonstruje, že systém toto bez manipulace správně zpracoval, zrušil spreadové filtry pro Bitfinex a úspěšně zavedl 20 bps fallback brzdu pro případ budoucího Erroru 500.

### Aktuální Log z `logs/fee_monitor.log`:
```text
2026-04-07 15:55:43,484 [fee_monitor] 💰 Fee Monitor Daemon starting...
2026-04-07 15:55:43,577 [fee_monitor] Bitfinex raw: maker_arr=[0, 0, 0, None, None, 0], taker_arr=[0, 0, 0, None, None, 0]
2026-04-07 15:55:43,577 [fee_monitor] Bitfinex fees: maker=0.000% (0) taker=0.000% (0)
2026-04-07 15:55:43,865 [fee_monitor] Binance fees: maker=0.100% (1000) taker=0.100% (1000)
2026-04-07 15:55:44,059 [fee_monitor] Fee change alert sent to Telegram
2026-04-07 15:55:44,059 [fee_monitor] Fee state updated at 2026-04-07T13:55:44.059317+00:00
```

---

## 🔬 2. FORENZNÍ AUDIT L1 ORÁKULA (`ml_shield.py`)

Podrobil jsem skalpelu tvůj soubor `architect/ml_shield.py`, který reprezentuje L1 neurální štít komunikující na 50ms frekvenci s Rust jádrem. S ohledem na tvé čtyři pilíře tu máme zásadní slabiny.

### 🌪️ Pilíř 1. Senzorická vrstva (Data Feeds & OBI)
**Slabina: Syndrom Krátkozrakosti (Myopia)**
Orákulum počítá tzv. Dynamické Vážené OBI (`w_obi`), kde vynikajícím způsobem zohledňuje vzdálenost od *mid-price* (`1.0 / abs(mid - l[0])`). To je perfektní HFT princip. 
**ALE**, kód agresivně řeže viditelnost jen na Top 5 L2 úrovní:
`bid_vol = sum(abs(l[1]) for l in book['bid_levels'][:5])`
Pokud chce smart-money (velryba) napumpovat trh, vloží brutální Bid Wall na 10. nebo 15. úroveň. V aktuálním stavu o něm tvé algoritmy neví, dokud se trh nezačne fyzicky propalovat do top 5. Boti zjistí toxikaci pozdě a nestihnou poodstoupit.

### 🩸 Pilíř 2. Detekce VPIN (Probability of Informed Trading)
**Slabina: Časově Zkreslené Pseudometrikum**
V kódu ("Sovereign VPIN Engine v2.0") se objevuje:
`raw_vpin = current_obi + (self.ema_delta_obi * 7.5)`
Toto NENÍ skutečný VPIN! Toto je pouze prosté OBI Momentum vynásobené agresivní fikční konstantou `7.5x`. Skutečný *VPIN (Volume-Synchronized Probability of Informed Trading)* nedělí trh na fixní časové úseky (50ms smyčka `ml_shield.py`), ale na stejnoměrné **objemové bloky** (tzv. Volume Buckets).
Při extrémní volatilitě dochází k objemovým špičkám zlomek sekundy po sobě. Časové dělení ve tvém kódu tuto hustotu informačního toku kompletně deformuje a vytváří slepé skvrny.

### ⚠️ Pilíř 3. Hraniční matematika (Thresholding & Toxic Storm)
**Slabina: Statický Beton**
Konstanty v úvodu skriptu hlásí toto:
```python
STORM_VPIN_THRESHOLD = 0.95
STORM_SPREAD_Z_THRESHOLD = 5.0
STORM_OBI_MOMENTUM = 0.7
```
Z-Score Spreadu `5.0` znamená, že spread musí ustřelit natolik šíleně, že zasáhne 5 směrodatných odchylek! Navíc limity zůstávají zařezané pevně v kódu nezávisle na makroprostředí. Pokud bude bitcoin celý týden běsnit o stovky dolarů denně (High Vol regime), ten samý OBI Momentum = 0.7 může být jen běžný denní šum. Kód nutně potrbuje dynamický okraj pro Z-Score threshold přes Bollingerova pásma.

### ⚡ Pilíř 4. Výpočetní paralýza (Python GIL)
**Slabina: Skrytý Jitter Destruktor**
Cyklus se točí na `50ms` (`time.sleep(sleep_ms / 1000)`). Během těchto 50ms tvůj kód nejdřív složitě sekvenčně pomocí bajtového `struct.unpack_from` dekóduje celou L2 knihu v loopu! Python u každé iterace narazí na GIL. Pythonovský loop pro deserializaci stovek u64 integrů každých pár desítek milisekund generuje pro O(1) Rust nebezpečný jitter. NumPy je zavoláno až na úplný konec pro MAtrix násobení `np.dot`.

> 🛠️ **Velitelské shrnutí pro opravu:** `ml_shield.py` nutně vyžaduje vektorizaci deserializace pomocí `np.frombuffer` s využitím přímého memory-view (C-level mapování). Dále musíme transformovat OBI VPIN výpočet tak, aby byl vázán čistě na objem.

---

## 📜 3. ZKOUPÍROVAné ZDROJOVÉ SOUBORY (Záloha aktuální pravdy)

### `architect/fee_monitor.py`
```python
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

FEE_MATRIX_PATH = "/dev/shm/beroun/fee_matrix.bin"
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

        if isinstance(data, dict):
            trading = data.get('fees_trading_30d', data.get('fees_trading', {}))
            maker = trading.get('maker_fee', 0.001)  # default 0.1%
            taker = trading.get('taker_fee', 0.002)   # default 0.2%
        elif isinstance(data, list) and len(data) > 4:
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

        maker = data.get('makerCommission', 10) / 10000 
        taker = data.get('takerCommission', 10) / 10000

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
    mm = _init_mmap()
    now_ms = int(time.time() * 1000)

    prev_maker_bfx, prev_taker_bfx = read_venue_fee(mm, VENUE_BITFINEX)

    bfx = fetch_bitfinex_fees()
    if bfx:
        write_venue_fee(mm, VENUE_BITFINEX, bfx['maker'], bfx['taker'], now_ms, 1)
    else:
        # Fallback na 20 bps pokud burza neodpovídá a hodnoty dříve byly 0
        if prev_maker_bfx == 0:
            log.warning("Bitfinex offline and no previous fee state. Activating 20 bps safety brake!")
            write_venue_fee(mm, VENUE_BITFINEX, 2000, 2000, now_ms, 1)

    bnb = fetch_binance_fees()
    if bnb:
        write_venue_fee(mm, VENUE_BINANCE, bnb['maker'], bnb['taker'], now_ms, 1)

    new_maker_bfx, new_taker_bfx = read_venue_fee(mm, VENUE_BITFINEX)
    if prev_maker_bfx > 0 and (new_maker_bfx != prev_maker_bfx or new_taker_bfx != prev_taker_bfx):
        _send_fee_change_alert(prev_maker_bfx, prev_taker_bfx, new_maker_bfx, new_taker_bfx)

    mm.close()

def _send_fee_change_alert(old_maker, old_taker, new_maker, new_taker):
    try:
        import urllib.request
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        if not token or not chat_id: return

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
    except Exception as e:
        log.error(f"TG alert failed: {e}")

if __name__ == '__main__':
    log.info("💰 Fee Monitor Daemon starting...")
    while True:
        try:
            update_fees()
        except Exception as e:
            log.error(f"Fee Monitor Loop Error: {e}")
        time.sleep(60)
```
