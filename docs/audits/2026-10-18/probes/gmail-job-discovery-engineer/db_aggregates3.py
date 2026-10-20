import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yahbah.config import settings

QUERIES = {
    "sj_dup_by_path": "select count(*) paths_dup, coalesce(sum(c),0) rows_in_dup, coalesce(sum(c-1),0) extra_rows from (select split_part(job_url,'?',1) p, count(*) c from scheduled_job group by 1 having count(*)>1) t",
    "sj_distinct_paths": "select count(distinct split_part(job_url,'?',1)), count(*) from scheduled_job",
    "dup_runs_by_path": "select count(*) from (select split_part(job_url,'?',1) p from application_run where job_url like '%jobright%' group by 1 having count(*)>1) t",
    "imp_id_len": "select length(split_part(split_part(job_url,'imp_id=',2),'&',1)) l, count(*) from scheduled_job where job_url like '%imp_id=%' group by 1",
}
async def main():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:
        await conn.execute(text("set transaction read only"))
        for name, q in QUERIES.items():
            rows = (await conn.execute(text(q))).all()
            print(f"== {name}")
            for r in rows: print("  ", tuple(r))
    await engine.dispose()
asyncio.run(main())
