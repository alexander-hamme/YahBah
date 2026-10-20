import asyncio, logging, sys
import httpx
logging.disable(logging.CRITICAL)
from yahbah.api.main import app

async def main():
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for p in sys.argv[1:]:
            try:
                r = await c.get(p)
                body = r.json() if r.headers.get("content-type","").startswith("application/json") else r.text[:200]
                if isinstance(body, dict) and "items" in body:
                    print(p, r.status_code, "total=", body.get("total"), "item keys=", sorted(body["items"][0].keys()) if body["items"] else [])
                elif isinstance(body, list):
                    print(p, r.status_code, "len=", len(body), "keys=", sorted(body[0].keys()) if body else [])
                elif isinstance(body, dict) and len(body) < 12 and all(isinstance(v,(int,float,bool)) for v in body.values()):
                    print(p, r.status_code, body)
                else:
                    print(p, r.status_code, "keys=", sorted(body.keys()) if isinstance(body, dict) else type(body).__name__)
            except Exception as e:
                print(p, "EXC", type(e).__name__, str(e).splitlines()[0][:300])

asyncio.run(main())
