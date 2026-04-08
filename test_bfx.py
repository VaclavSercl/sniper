import os
import time
import json
import hmac
import hashlib
import urllib.request
import urllib.error

# Load .env
with open('/home/wwwenda/sniper/.env') as f:
    for line in f:
        line=line.strip()
        if not line or line.startswith('#'): continue
        if '=' in line:
            k,v = line.split('=', 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')

key = os.environ.get('BITFINEX_API_KEY', '')
secret = os.environ.get('BITFINEX_API_SECRET', '')

path = '/v2/auth/r/summary'
nonce = str(int(time.time() * 100000))
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
        print("Success!", resp.read().decode())
except urllib.error.HTTPError as e:
    print(f"HTTPError: {e.code} {e.reason}")
    print("Body:", e.read().decode())
except Exception as e:
    print("Error:", e)
