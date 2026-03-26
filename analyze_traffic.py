import asyncio
import websockets
import json
import time
import os
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BITFINEX_API_KEY")
API_SECRET = os.getenv("BITFINEX_API_SECRET")

async def analyze_bitfinex():
    url = "wss://api.bitfinex.com/ws/2"
    log_file = "traffic_analysis.log"
    
    print(f"Starting Bitfinex Traffic Analysis for 66 seconds...")
    print(f"Logging to {log_file}")
    
    with open(log_file, "w") as f:
        f.write(f"--- Analysis Started at {time.ctime()} ---\n")

    try:
        async with websockets.connect(url) as ws:
            # 1. Configuration (Enable Checksum)
            conf_msg = {"event": "conf", "flags": 131072} # OB_CHECKSUM
            await ws.send(json.dumps(conf_msg))
            
            # 2. Authentication
            nonce = str(int(time.time() * 1000))
            auth_payload = "AUTH" + nonce
            signature = hmac.new(
                API_SECRET.encode(),
                auth_payload.encode(),
                hashlib.sha384
            ).hexdigest()
            
            auth_msg = {
                "event": "auth",
                "apiKey": API_KEY,
                "authSig": signature,
                "authPayload": auth_payload,
                "authNonce": nonce,
                "dms": 4
            }
            await ws.send(json.dumps(auth_msg))
            
            # 3. Subscribe to Book
            sub_msg = {
                "event": "subscribe",
                "channel": "book",
                "symbol": "tBTCUSD",
                "prec": "P0",
                "freq": "F0",
                "len": "25"
            }
            await ws.send(json.dumps(sub_msg))
            
            start_time = time.time()
            msg_count = 0
            
            while time.time() - start_time < 66:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    msg_count += 1
                    with open(log_file, "a") as f:
                        f.write(f"[{time.time() - start_time:.2f}s] {response}\n")
                    
                    # Print status every 10 seconds
                    if int(time.time() - start_time) % 10 == 0 and int(time.time() - start_time) > 0:
                        print(f"Still capturing... {int(66 - (time.time() - start_time))}s remaining. Received {msg_count} messages.")
                        # Small sleep to avoid spamming the same second
                        await asyncio.sleep(1)
                        
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"Error during capture: {e}")
                    break
                    
            print(f"Analysis complete. Total messages captured: {msg_count}")
            
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(analyze_bitfinex())
