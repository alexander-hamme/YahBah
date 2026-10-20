import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yahbah.config import settings
async def main():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:
        await conn.execute(text("set transaction read only"))
        rows = (await conn.execute(text("select r.id from application_run r where r.job_url like '%jobright%' order by created_at limit 400"))).all()
        print("\n".join(str(r[0]) for r in rows))
    await engine.dispose()
asyncio.run(main())
