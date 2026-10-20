"""Read-only: final-page host in failure messages of jobright runs (counts only)."""
import asyncio, logging, re
from collections import Counter
from urllib.parse import urlparse
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await s.execute(text("select error_message from application_run where job_url like '%jobright%'"))).all()
        hosts = Counter(); same = 0
        for (m,) in rows:
            u = re.search(r"url='([^']+)'", m or "")
            hosts[urlparse(u.group(1)).netloc if u else None] += 1
        print(hosts.most_common(10))
        steps = (await s.execute(text(
            "select st.step_name, st.status, count(*) from application_step st join application_run r on r.id = st.run_id "
            "where r.job_url like '%jobright%' group by 1,2 order by 3 desc limit 15"))).all()
        print(steps)
asyncio.run(main())
