import asyncio
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        try:
            print("select1:", (await s.execute(text("select 1"))).scalar_one())
            print("alembic:", (await s.execute(text("select version_num from alembic_version"))).scalar_one())
            rows = (await s.execute(text("select status, count(*) from application_run group by status"))).all()
            print("run status counts:", rows)
            rows = (await s.execute(text("select status, count(*) from scheduled_job group by status"))).all()
            print("scheduled counts:", rows)
        except Exception as e:
            print("ERR", type(e).__name__, str(e).splitlines()[0][:300])

asyncio.run(main())
