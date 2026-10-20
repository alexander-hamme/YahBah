"""Read-only aggregates over application_run / scheduled_job. No personal data printed."""
import asyncio, re
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal

Q = {
 "runs by status": "select status, count(*) from application_run group by 1 order by 2 desc",
 "runs by month,status": "select to_char(created_at,'YYYY-MM') m, status, count(*) from application_run group by 1,2 order by 1,2",
 "runs by current_state for non-terminal": "select status, current_state, count(*) from application_run where status not in ('COMPLETED','FAILED','DUPLICATE','CANCELLED') group by 1,2 order by 3 desc",
 "failed by state": "select current_state, count(*) from application_run where status='FAILED' group by 1 order by 2 desc",
 "by ats_type,status": "select coalesce(j.ats_type,'?') ats, r.status, count(*) from application_run r left join job_posting j on j.id=r.job_posting_id group by 1,2 order by 1,3 desc",
 "submit tracking": "select count(*) filter (where submit_attempted_at is not null) attempted, count(*) filter (where email_confirmed_at is not null) email_confirmed, count(*) filter (where is_test_run) test_runs, count(*) filter (where confirmation_html is not null) has_conf_html, min(created_at)::date, max(created_at)::date from application_run",
 "completed w/o confirmation evidence": "select count(*) from application_run where status='COMPLETED' and confirmation_html is null and email_confirmed_at is null and not is_test_run",
 "since agentic (2026-10-01)": "select status, current_state, count(*) from application_run where created_at >= '2026-10-01' group by 1,2 order by 3 desc",
 "scheduled_job by status": "select status, count(*) from scheduled_job group by 1 order by 2 desc",
 "match score distribution (runs)": "select width_bucket(match_score,0,100,10) b, count(*) from application_run where match_score is not null group by 1 order by 1",
 "url hosts": "select substring(job_url from '^https?://([^/]+)') host, status, count(*) from application_run group by 1,2 order by 3 desc limit 40",
 "dup job_posting per canonical": "select count(*) from (select canonical_url from job_posting where canonical_url is not null group by 1 having count(*)>1) x",
 "runs per job_posting >1": "select count(*) from (select job_posting_id from application_run group by 1 having count(*)>1) x",
 "completed runs per posting >1": "select count(*) from (select job_posting_id from application_run where status='COMPLETED' and not is_test_run group by 1 having count(*)>1) x",
}

def err_class(msg: str) -> str:
    if not msg: return "(none)"
    m = msg.strip()
    m = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", m)
    head = re.split(r"[:\n]", m, 1)[0][:70]
    return head

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for name, q in Q.items():
            try:
                rows = (await s.execute(text(q))).all()
            except Exception as e:
                print(f"## {name}: ERROR {type(e).__name__}: {str(e)[:120]}"); await s.rollback(); await s.execute(text("SET TRANSACTION READ ONLY")); continue
            print(f"## {name}")
            for r in rows: print("  ", tuple(r))
        rows = (await s.execute(text("select to_char(created_at,'YYYY-MM'), current_state, error_message from application_run where status='FAILED'"))).all()
        from collections import Counter
        c = Counter((m, st, err_class(e)) for m, st, e in rows)
        print("## failure classes (month, state, error head)")
        for k, v in sorted(c.items(), key=lambda kv: (-kv[1])): print("  ", v, k)

asyncio.run(main())
