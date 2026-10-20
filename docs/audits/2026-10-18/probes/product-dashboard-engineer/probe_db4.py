import asyncio, logging
logging.disable(logging.CRITICAL)
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        print("steps", (await s.execute(text("select count(*), count(distinct run_id) from application_step"))).all())
        print("latest run", (await s.execute(text("select max(created_at) from application_run"))).all())
asyncio.run(main())
