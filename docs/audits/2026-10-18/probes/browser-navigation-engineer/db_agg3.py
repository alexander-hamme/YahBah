"""Read-only: keyword counts over fetched jobright page text in scheduled_job.description (no text output)."""
import asyncio, logging, re
from collections import Counter
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
KW = ["Apply Now", "Apply on", "APPLY", "Sign in", "Sign up", "Log in", "Login", "original", "Original",
      "Easy Apply", "greenhouse", "lever", "workday", "myworkdayjobs", "ashby", "icims", "smartrecruiters",
      "Jobright", "Turbo", "Autofill", "expired", "no longer"]
async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        rows = [r[0] or "" for r in (await s.execute(text("select description from scheduled_job"))).all()]
        print("n", len(rows), "nonempty", sum(1 for r in rows if r), "avg len", sum(map(len, rows)) // max(1, len(rows)))
        c = Counter()
        for r in rows:
            for k in KW:
                if k in r: c[k] += 1
        print(c.most_common())
        # first 3 words of each description (page header shape), counted
        heads = Counter(" ".join(r.split()[:4]) for r in rows if r)
        print(heads.most_common(5))
asyncio.run(main())
