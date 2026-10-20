import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yahbah.config import settings
QUERIES = {
    "company_hosted_urls": "select regexp_replace(regexp_replace(split_part(r.job_url,'://',2), '[0-9]{3,}', 'N', 'g'), '/[a-z0-9-]{25,}', '/<slug>', 'g') shape, r.status, (p.canonical_url is not null) has_canon, split_part(split_part(coalesce(p.canonical_url,''),'://',2),'/',1) canon_host from application_run r join job_posting p on p.id=r.job_posting_id where r.job_url not like '%greenhouse.io%' and r.job_url not like '%jobright%' order by 1",
    "gh_jid_in_canonical": "select count(*) from job_posting where canonical_url like '%gh_jid%' or url like '%gh_jid%'",
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
