import asyncio, logging
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal, engine
engine.echo = False
Q = {
 "jobright burst window": "select min(created_at), max(created_at), count(*), round(avg(extract(epoch from (updated_at-created_at)))::numeric,1) avg_secs, max(extract(epoch from (updated_at-created_at)))::int max_secs from application_run where job_url like '%jobright.ai%'",
 "jobright distinct urls": "select count(distinct job_url) from application_run where job_url like '%jobright.ai%'",
 "jobright runs per minute peak": "select date_trunc('minute', created_at) m, count(*) from application_run where job_url like '%jobright.ai%' group by 1 order by 2 desc limit 3",
 "scheduled_job hosts": "select substring(job_url from '^https?://([^/]+)') host, status, count(*) from scheduled_job group by 1,2 order by 3 desc limit 10",
 "scheduled_job source": "select source, count(*) from scheduled_job group by 1",
 "scheduled match score": "select width_bucket(match_score,0,100,10) b, count(*) from scheduled_job group by 1 order by 1",
 "completed duration": "select round(avg(extract(epoch from (completed_at-created_at)))::numeric/60,1) avg_min, round(max(extract(epoch from (completed_at-created_at)))::numeric/60,1) max_min from application_run where status='COMPLETED'",
 "steps by name,status": "select step_name, status, count(*) from application_step group by 1,2 order by 3 desc limit 25",
 "artifacts by type": "select artifact_type, count(*) from application_artifact group by 1 order by 2 desc",
 "answer_cache rows": "select count(*), sum(hits) from answer_cache",
 "status updates": "select status_type, count(*) from application_status_update group by 1",
 "alembic": "select version_num from alembic_version",
}
async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for n, q in Q.items():
            try: rows = (await s.execute(text(q))).all()
            except Exception as e:
                print("##", n, "ERROR", str(e)[:150]); await s.rollback(); await s.execute(text("SET TRANSACTION READ ONLY")); continue
            print("##", n); [print("  ", tuple(r)) for r in rows]
asyncio.run(main())
