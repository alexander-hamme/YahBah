import asyncio, logging, re
from collections import Counter
logging.disable(logging.CRITICAL)
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("select confirmation_html, is_test_run from application_run where confirmation_html is not null"))).all()
        for html, test in rows:
            tags = Counter(t.lower() for t in re.findall(r"<([a-zA-Z][a-zA-Z0-9]*)", html))
            handlers = sorted(set(h.lower() for h in re.findall(r"\s(on[a-z]+)\s*=", html, re.I)))
            print("test" if test else "real", "len", len(html), "active tags:", {k: v for k, v in tags.items() if k in ("script","iframe","img","svg","form","input","button","a","style","link","object","embed")}, "handlers:", handlers)

asyncio.run(main())
