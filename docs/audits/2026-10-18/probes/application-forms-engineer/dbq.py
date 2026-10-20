import asyncio, sys
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal

QUERIES = {
 "status": "select status, count(*) from application_run group by 1 order by 2 desc",
 "state_failed": "select coalesce(current_state,'-'), count(*) from application_run where status='FAILED' group by 1 order by 2 desc",
 "attempted": "select status, (submit_attempted_at is not null) att, (email_confirmed_at is not null) emailc, is_test_run, count(*) from application_run group by 1,2,3,4 order by 5 desc",
 "errs": "select left(regexp_replace(coalesce(error_message,''), '[0-9a-f]{8}-[0-9a-f-]{27}', '<id>', 'g'),90) e, count(*) from application_run where status='FAILED' group by 1 order by 2 desc limit 25",
 "ats": "select ats_type, count(*) from job_posting group by 1 order by 2 desc",
 "range": "select min(created_at), max(created_at), count(*) filter (where created_at > '2026-10-12') from application_run",
 "jr": "select count(*) filter (where error_message ilike '%jobright%') jr, count(*) filter (where error_message not ilike '%jobright%') other, count(*) from application_run where status='FAILED'",
 "other_err": "select current_state, left(error_message,160), created_at::date from application_run where status='FAILED' and error_message not ilike '%jobright%' order by created_at desc limit 12",
 "completed_by_ats": "select jp.ats_type, count(*), min(ar.completed_at)::date, max(ar.completed_at)::date from application_run ar join job_posting jp on jp.id=ar.job_posting_id where ar.status='COMPLETED' group by 1",
 "tracking": "select count(*) filter (where tracking_url is not null) trk, count(*) filter (where confirmation_html is not null) conf, count(*) from application_run where status='COMPLETED'",
}
async def main():
    async with AsyncSessionLocal() as s:
        for name in sys.argv[1:] or QUERIES:
            print("==", name)
            res = await s.execute(text(QUERIES[name]))
            for r in res.all(): print(tuple(r))
asyncio.run(main())
