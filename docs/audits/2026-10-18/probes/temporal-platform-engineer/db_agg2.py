import asyncio
from sqlalchemy import text
from yahbah.db.session import engine
QUERIES = {
 "runs_per_10min_top": "select date_trunc('hour', created_at) + floor(extract(minute from created_at)/10)*interval '10 min' b, count(*) from application_run group by 1 order by 2 desc limit 8",
 "promoted_runs_span": "select min(r.created_at), max(r.created_at), count(*), max(r.completed_at) from scheduled_job s join application_run r on r.id=s.promoted_run_id",
 "promoted_run_durations": "select percentile_cont(array[0.5,0.9,0.99]) within group (order by extract(epoch from (r.completed_at-r.created_at))) from scheduled_job s join application_run r on r.id=s.promoted_run_id",
 "promoted_host": "select split_part(split_part(s.job_url,'://',2),'/',1) h, count(*) from scheduled_job s group by 1 order by 2 desc limit 6",
 "completed_durations": "select percentile_cont(array[0.5,0.9,1.0]) within group (order by extract(epoch from (completed_at-created_at))) from application_run where status='COMPLETED'",
}
async def main():
    async with engine.connect() as c:
        await c.execute(text("SET TRANSACTION READ ONLY"))
        for k,q in QUERIES.items():
            rows = (await c.execute(text(q))).all()
            print(f"== {k}")
            for r in rows: print("  ", tuple(r))
asyncio.run(main())
