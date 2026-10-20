import asyncio
from sqlalchemy import text
from yahbah.db.session import engine

QUERIES = {
 "status_by_state": "select status, current_state, count(*) from application_run group by 1,2 order by 3 desc",
 "is_test_by_status": "select status, is_test_run, count(*) from application_run group by 1,2 order by 1",
 "submit_attempted_by_status": "select status, (submit_attempted_at is not null) as attempted, (email_confirmed_at is not null) as confirmed, count(*) from application_run group by 1,2,3 order by 1",
 "error_prefix": "select left(coalesce(error_message,''),60) e, count(*) from application_run where status in ('FAILED','DUPLICATE') group by 1 order by 2 desc limit 25",
 "postings_multi_runs": "select n, count(*) from (select job_posting_id, count(*) n from application_run group by 1) t group by 1 order by 1",
 "postings_multi_completed": "select count(*) from (select job_posting_id from application_run where status='COMPLETED' and not is_test_run group by 1 having count(*)>1) t",
 "canonical_multi_completed": "select count(*) from (select jp.canonical_url from application_run r join job_posting jp on jp.id=r.job_posting_id where r.status='COMPLETED' and not r.is_test_run and jp.canonical_url is not null group by 1 having count(*)>1) t",
 "nonterminal_age": "select status, current_state, min(updated_at), max(updated_at), count(*) from application_run where status in ('PENDING','RUNNING') group by 1,2",
 "alembic": "select version_num from alembic_version",
 "scheduled_status": "select status, hold, count(*) from scheduled_job group by 1,2 order by 1",
 "scheduled_dup_urls": "select count(*) from (select job_url from scheduled_job group by 1 having count(*)>1) t",
 "scheduled_overdue": "select status, count(*) from scheduled_job where promote_after < now() and status in ('SCHEDULED','APPROVED') group by 1",
 "run_dates": "select min(created_at), max(created_at), count(*) from application_run",
 "worker_restart_failures": "select count(*) from application_run where error_message like 'Worker%'",
 "steps_count": "select count(*) from application_step",
 "indexes": "select tablename, indexname, indexdef from pg_indexes where schemaname='public' order by 1,2",
 "constraints": "select conrelid::regclass, conname, contype from pg_constraint where connamespace='public'::regnamespace order by 1,2",
}
async def main():
    async with engine.connect() as c:
        await c.execute(text("SET TRANSACTION READ ONLY"))
        for k,q in QUERIES.items():
            rows = (await c.execute(text(q))).all()
            print(f"== {k}")
            for r in rows: print("  ", tuple(r))
asyncio.run(main())
