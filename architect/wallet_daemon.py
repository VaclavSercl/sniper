import os
import time
import json
import logging
import requests
import hashlib
import hmac
import mmap
import struct

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("wallet_daemon")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env manually
env_file = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(env_file):
    with open(env_file, "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

TOTAL_EQUITY_OFFSET = 16  # In ArmadaStateV2 (armada_state_v2.bin)
WALLET_BTC_OFFSET = 1432  # In EngineState
WALLET_USD_OFFSET = 1440  # In EngineState

BOT_FILES = {
    "hydra": "engine_state.bin",
    "moonshot": "moonshot_engine.bin",
    "grid": "grid_engine.bin",
    "trigon": "trigon_engine.bin",
    "nexus": "cross_exchange.bin"
}

def fetch_bitfinex(api_key, api_secret):
    url = "https://api.bitfinex.com/v2/auth/r/wallets"
    nonce = str(int(time.time() * 1000000))
    signature = f"/api/v2/auth/r/wallets{nonce}{{}}".encode("utf-8")
    h = hmac.new(api_secret.encode("utf-8"), signature, hashlib.sha384)
    signature_hex = h.hexdigest()
    
    headers = {
        "bfx-nonce": nonce,
        "bfx-apikey": api_key,
        "bfx-signature": signature_hex,
        "content-type": "application/json"
    }

    try:
        res = requests.post(url, headers=headers, json={}, timeout=5)
        data = res.json()
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
            usd_bal = 0.0
            btc_bal = 0.0
            for w in data:
                if w[0] == "exchange":
                    if w[1] == "USD": usd_bal += float(w[2])
                    elif w[1] == "BTC": btc_bal += float(w[2])
            return usd_bal, btc_bal
    except Exception as e:
        log.error(f"Bitfinex fetch error: {e}")
    return 0.0, 0.0

def fetch_binance(api_key, api_secret):
    url = "https://api.binance.com/api/v3/account"
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    
    headers = {"X-MBX-APIKEY": api_key}
    try:
        res = requests.get(f"{url}?{query_string}&signature={signature}", headers=headers, timeout=5)
        data = res.json()
        usd_bal = 0.0
        btc_bal = 0.0
        for balance in data.get('balances', []):
            asset = balance['asset']
            total = float(balance['free']) + float(balance['locked'])
            if asset in ['USDT', 'USDC', 'USD']: usd_bal += total
            elif asset == 'BTC': btc_bal += total
        return usd_bal, btc_bal
    except Exception as e:
        log.error(f"Binance fetch error: {e}")
    return 0.0, 0.0

EXCHANGE_ADAPTERS = {
    "BITFINEX": fetch_bitfinex,
    "BINANCE": fetch_binance,
}

def sync_all_wallets():
    balances = {}
    total_usd = 0.0
    total_btc = 0.0
    active_venues = 0
    
    for prefix, adapter in EXCHANGE_ADAPTERS.items():
        key = os.environ.get(f"{prefix}_API_KEY")
        secret = os.environ.get(f"{prefix}_API_SECRET")
        if key and secret:
            u, b = adapter(key, secret)
            balances[prefix] = (u, b)
            if u > 0 or b > 0:
                log.info(f"[{prefix}] Wallet loaded: ${u:.2f} | ₿ {b:.6f}")
                total_usd += u
                total_btc += b
                active_venues += 1
                
    if active_venues == 0:
        log.warning("No API keys yielded results. Using fallback ($800.0, ₿ 0.1)")
        balances["BITFINEX"] = (800.0, 0.1)
        total_usd = 800.0
        total_btc = 0.1
        
    return balances, total_usd, total_btc

def write_to_mmap():
    balances, total_usd, total_btc = sync_all_wallets()
    
    btc_price = 70000.0
    try:
        res = requests.get("https://api.bitfinex.com/v2/ticker/tBTCUSD", timeout=5)
        if res.status_code == 200:
            data = res.json()
            btc_price = data[6]
    except:
        pass
        
    total_eq = total_usd + (total_btc * btc_price)
    log.info(f"💰 Global Aggregated Sync: ${total_usd:.2f} | ₿ {total_btc:.6f} | Total: ${total_eq:.2f}")
    
    SCALE = 100_000_000
    total_eq_scaled = int(total_eq * SCALE)
    
    v2_path = "/dev/shm/sniper/armada_state_v2.bin"
    if os.path.exists(v2_path):
        try:
            with open(v2_path, "r+b") as f:
                mm = mmap.mmap(f.fileno(), 0)
                struct.pack_into("=Q", mm, TOTAL_EQUITY_OFFSET, total_eq_scaled)
        except Exception as e:
            log.error(f"Write to ArmadaStateV2 failed: {e}")
            
    # ONLY map Bitfinex wallet to the standard bots, as they trade exclusively on Bitfinex.
    # Nexus doesn't use `wallet_usd/btc`, it uses ArmadaCapitalMatrix.
    bfx_usd, bfx_btc = balances.get("BITFINEX", (0.0, 0.0))
    bfx_usd_scaled = int(bfx_usd * SCALE)
    bfx_btc_scaled = int(bfx_btc * SCALE)
    
    for bot, filename in BOT_FILES.items():
        if bot == "nexus": continue # Skip Nexus for legacy wallets
        
        engine_path = f"/dev/shm/sniper/{filename}"
        if os.path.exists(engine_path):
            try:
                with open(engine_path, "r+b") as f:
                    mm = mmap.mmap(f.fileno(), 0)
                    struct.pack_into("=Q", mm, WALLET_BTC_OFFSET, bfx_btc_scaled)
                    struct.pack_into("=Q", mm, WALLET_USD_OFFSET, bfx_usd_scaled)
            except Exception as e:
                log.error(f"Failed to write to {engine_path}: {e}")
                
    # Dump JSON for dashboard
    try:
        bnb_usd, bnb_btc = balances.get("BINANCE", (0.0, 0.0))
        wallets_json = {
            "GLOBAL": {"total_usd": total_eq, "usd": total_usd, "btc": total_btc},
            "BITFINEX": {"usd": bfx_usd, "btc": bfx_btc, "total_usd": bfx_usd + (bfx_btc * btc_price)},
            "BINANCE": {"usd": bnb_usd, "btc": bnb_btc, "total_usd": bnb_usd + (bnb_btc * btc_price)},
            "btc_price": btc_price
        }
        with open("/dev/shm/sniper/wallets.json", "w") as f:
            json.dump(wallets_json, f)
    except Exception as e:
        log.error(f"Failed to dump wallets.json: {e}")

def main():
    log.info("🛡️ Multi-Venue Wallet Daemon started. Syncing every 60s.")
    while True:
        try:
            write_to_mmap()
        except Exception as e:
            log.error(f"Daemon loop error: {e}")
        time.sleep(60)

if __name__ == "__main__":
    main()
