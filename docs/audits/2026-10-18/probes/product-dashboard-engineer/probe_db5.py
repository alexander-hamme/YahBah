import asyncio, logging, re
from collections import Counter
logging.disable(logging.CRITICAL)
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""select r.error_message, (s.id is not null) as from_sched, r.created_at::date
            from application_run r left join scheduled_job s on s.promoted_run_id = r.id where r.status='FAILED'"""))).all()
        c = Counter()
        for msg, sched, d in rows:
            m = (msg or "").strip()
            key = re.split(r"[:(\n]", m, 1)[0][:40]
            key = re.sub(r"https?://\S+", "<url>", key)
            c[(key, sched)] += 1
        for (k, sched), n in c.most_common(8):
            print(n, "from_scheduled" if sched else "direct", repr(k))
        print("date range", min(r[2] for r in rows), max(r[2] for r in rows))
asyncio.run(main())
