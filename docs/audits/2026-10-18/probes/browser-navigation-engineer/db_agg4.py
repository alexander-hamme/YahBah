"""Read-only: boilerplate phrases around APPLY / Autofill in jobright page text (counts only)."""
import asyncio, logging, re
from collections import Counter
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        rows = [r[0] or "" for r in (await s.execute(text("select description from scheduled_job"))).all()]
        c = Counter()
        for r in rows:
            for m in re.finditer(r"(\S+ ){0,2}APPLY( \S+){0,3}", r): c[m.group(0)] += 1
            for m in re.finditer(r"(\S+ ){0,3}Autofill( \S+){0,3}", r): c[m.group(0)] += 1
            for m in re.finditer(r"(\S+ ){0,2}[Ll]ever( \S+){0,2}", r): c["lever: " + m.group(0)] += 1
        for k, n in c.most_common(25): print(n, k[:100])
asyncio.run(main())
