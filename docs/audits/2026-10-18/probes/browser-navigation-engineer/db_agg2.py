"""Read-only: host distribution of scheduled/queued job URLs (no PII)."""
import asyncio, logging, re
from urllib.parse import urlparse
from collections import Counter
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

def shape(u):
    p = urlparse(u)
    path = re.sub(r"\d{3,}", "N", p.path)
    path = re.sub(r"/[0-9a-f]{16,}", "/H", path)
    return p.netloc + "/".join(path.split("/")[:3])

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        sj = (await s.execute(text("select job_url, source, status from scheduled_job"))).all()
        print("scheduled_job total", len(sj))
        print("by source/status", Counter((r[1], r[2]) for r in sj).most_common(20))
        print("hosts", Counter(urlparse(r[0]).netloc for r in sj).most_common(25))
        print("shapes", Counter(shape(r[0]) for r in sj).most_common(15))
        jp = (await s.execute(text("select url, canonical_url, ats_type from job_posting"))).all()
        print("job_posting total", len(jp), Counter(r[2] for r in jp).most_common())
        print("canonical hosts", Counter(urlparse(r[1] or '').netloc for r in jp).most_common(15))
        runs = (await s.execute(text("select r.job_url, r.status from application_run r where r.job_url like '%jobright%' limit 400"))).all()
        print("jobright run shapes", Counter(shape(r[0]) for r in runs).most_common(5))
asyncio.run(main())
