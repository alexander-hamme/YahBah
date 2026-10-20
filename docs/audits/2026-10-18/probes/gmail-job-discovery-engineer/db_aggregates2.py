import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yahbah.config import settings

QUERIES = {
    "runs_by_month": "select to_char(created_at,'YYYY-MM') m, status, count(*) from application_run group by 1,2 order by 1,2",
    "runs_latest": "select max(created_at)::timestamp(0), max(updated_at)::timestamp(0) from application_run",
    "asu_latest": "select min(created_at)::date, max(created_at)::date, max(email_date)::date from application_status_update",
    "sj_promote_times": "select min(promote_after)::date, max(promote_after)::date from scheduled_job",
    "sj_per_msg": "select n, count(*) from (select gmail_message_id, count(*) n from scheduled_job group by 1) t group by 1 order by 1",
    "sj_recommend": "select count(*) from scheduled_job where job_url like '%/jobs/recommend%'",
    "sj_query_keys": "select k, count(*) from (select split_part(unnest(string_to_array(split_part(job_url,'?',2),'&')),'=',1) k from scheduled_job) t group by 1 order by 2 desc limit 15",
    "promoted_run_minutes": "select round(extract(epoch from (r.completed_at - r.created_at))/60) mins, count(*) from scheduled_job s join application_run r on r.id=s.promoted_run_id group by 1 order by 2 desc limit 8",
    "promoted_run_created": "select to_char(r.created_at,'YYYY-MM-DD') d, count(*) from scheduled_job s join application_run r on r.id=s.promoted_run_id group by 1 order by 1",
    "runs_canonical_hosts": "select split_part(split_part(p.canonical_url,'://',2),'/',1) host, count(*) from application_run r join job_posting p on p.id=r.job_posting_id where r.job_url like '%jobright%' group by 1 order by 2 desc limit 10",
    "asu_runs_multi_company": "select count(*) from (select r.id from application_status_update u join application_run r on r.id=u.run_id group by r.id having count(distinct u.status_type)>1) t",
}

async def main():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:
        await conn.execute(text("set transaction read only"))
        for name, q in QUERIES.items():
            try:
                rows = (await conn.execute(text(q))).all()
                print(f"== {name}")
                for r in rows: print("  ", tuple(r))
            except Exception as exc:
                print(f"== {name} ERROR {type(exc).__name__}: {str(exc)[:200]}")
                await conn.rollback(); await conn.execute(text("set transaction read only"))
    await engine.dispose()
asyncio.run(main())
