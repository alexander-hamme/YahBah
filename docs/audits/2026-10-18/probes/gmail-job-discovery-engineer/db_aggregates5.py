import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yahbah.config import settings
QUERIES = {
    "promo_spread": "select min(r.created_at)::timestamp(0), max(r.created_at)::timestamp(0), count(*) from scheduled_job s join application_run r on r.id=s.promoted_run_id",
    "promo_lateness_days": "select width_bucket(extract(epoch from (r.created_at - s.promote_after))/86400, 0, 60, 6) b, count(*) from scheduled_job s join application_run r on r.id=s.promoted_run_id group by 1 order by 1",
    "sj_age_at_promo_days": "select min(round(extract(epoch from (r.created_at - s.created_at))/86400)), max(round(extract(epoch from (r.created_at - s.created_at))/86400)) from scheduled_job s join application_run r on r.id=s.promoted_run_id",
    "sj_updated": "select to_char(updated_at,'YYYY-MM-DD') d, status, count(*) from scheduled_job group by 1,2 order by 1",
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
