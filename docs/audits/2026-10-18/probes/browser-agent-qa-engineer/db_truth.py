import asyncio
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal, engine
engine.echo = False
Q = {
 "status vs has company email (ground truth)": """
   select r.status, r.is_test_run, (exists (select 1 from application_status_update u where u.run_id=r.id)) has_email,
          (r.confirmation_html is not null) has_conf_html, count(*)
   from application_run r group by 1,2,3,4 order by 1,2,3,4""",
 "completed: created range by conf_html": "select (confirmation_html is not null) c, min(created_at)::date, max(created_at)::date, count(*) from application_run where status='COMPLETED' group by 1",
 "failed-but-company-emailed (false negatives)": "select r.current_state, count(*) from application_run r where r.status<>'COMPLETED' and exists (select 1 from application_status_update u where u.run_id=r.id) group by 1",
 "test runs status": "select status, count(*) from application_run where is_test_run group by 1",
}
async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for n, q in Q.items():
            rows = (await s.execute(text(q))).all()
            print("##", n); [print("  ", tuple(r)) for r in rows]
asyncio.run(main())
