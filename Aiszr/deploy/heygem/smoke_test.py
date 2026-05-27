"""15-line WS smoke test for HeyGem adapter on :8770."""
import asyncio, struct, httpx, websockets

HEAD_UP, HEAD_DOWN = ">q", ">qiiiii"
HEAD_DOWN_SIZE = struct.calcsize(HEAD_DOWN)

async def run():
    r = httpx.post("http://127.0.0.1:8770/v1/sessions/start",
                   json={"avatar_video_path": __file__, "wav_duration_ms": 1000}).json()
    sid = r["session_id"]; print(f"started session={sid[:8]}")
    async with websockets.connect(f"ws://127.0.0.1:8770/v1/sessions/{sid}/stream") as ws:
        for pts in (0, 40, 80, 120, 160):
            await ws.send(struct.pack(HEAD_UP, pts) + b"\x10\x00" * 960)
            msg = await ws.recv()
            p, idx, cx, cy, cw, ch = struct.unpack(HEAD_DOWN, msg[:HEAD_DOWN_SIZE])
            print(f"  pts={p:4d} idx={idx} crop=({cx},{cy},{cw},{ch}) body={len(msg)-HEAD_DOWN_SIZE}B")
    httpx.post(f"http://127.0.0.1:8770/v1/sessions/{sid}/stop"); print("stopped")

asyncio.run(run())
