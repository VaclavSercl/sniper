import asyncio
import websockets
import json

async def test_ws():
    try:
        async with websockets.connect("ws://127.0.0.1:3000/ws") as ws:
            msg = await ws.recv()
            print("RECEIVED:")
            parsed = json.loads(msg)
            print(json.dumps(parsed, indent=2))
    except Exception as e:
        print("ERROR:", e)

asyncio.run(test_ws())
