"""Read-only aggregate queries over application runs (no PII output)."""
import asyncio, re
from urllib.parse import urlparse
from collections import Counter
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal

def errclass(msg):
    if not msg: return None
    m = msg.strip().splitlines()[0]
    m = re.sub(r"https?://\S+", "<url>", m)
    m = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", m)
    m = re.sub(r"\d+", "N", m)
    return m[:110]

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await s.execute(text(
            "select status, current_state, job_url, error_message, created_at::date from application_run"))).all()
        print("total runs", len(rows))
        print(Counter(r[0] for r in rows).most_common())
        print(Counter((r[0], r[1]) for r in rows).most_common(25))
        hosts = Counter(urlparse(r[2]).netloc for r in rows)
        print("hosts:", hosts.most_common(40))
        print("by date:", sorted(Counter(str(r[4]) for r in rows).items()))
        fails = Counter((r[1], errclass(r[3])) for r in rows if r[0] == "FAILED")
        for k, n in fails.most_common(40): print(n, k)
        # failures by host
        fh = Counter(urlparse(r[2]).netloc for r in rows if r[0] == "FAILED")
        print("fail hosts:", fh.most_common(30))
import logging; logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
from yahbah.db import session as _s; _s.engine.echo = False if hasattr(_s, "engine") else None
asyncio.run(main())
