"""Read-only aggregate queries for the discovery audit. Prints counts only (no personal data)."""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from yahbah.config import settings

QUERIES = {
    "checkpoints": "select email as key, updated_at::date from gmail_checkpoint order by 1",
    "sj_by_status_source": "select status, source, count(*) from scheduled_job group by 1,2 order by 3 desc",
    "sj_score_null": "select status, count(*) filter (where match_score is null) as null_score, count(*) as n, min(match_score), max(match_score), round(avg(match_score)) from scheduled_job group by 1",
    "sj_score_hist": "select width_bucket(match_score,0,100,10) as bucket, count(*) from scheduled_job where match_score is not null group by 1 order by 1",
    "sj_desc_null": "select count(*) filter (where description is null) as no_desc, count(*) filter (where title is null) as no_title, count(*) filter (where company is null) as no_company, count(*) from scheduled_job",
    "sj_dup_urls": "select count(*) as urls_with_multiple_rows, coalesce(sum(c),0) as rows from (select job_url, count(*) c from scheduled_job group by 1 having count(*)>1) t",
    "sj_dup_urls_by_status": "select string_agg(status, ',' order by status) statuses, count(*) from (select job_url, status from scheduled_job where job_url in (select job_url from scheduled_job group by 1 having count(*)>1)) t group by job_url order by 2 desc limit 0",
    "sj_dup_status_combos": "select combo, count(*) from (select job_url, string_agg(status, ',' order by status) combo from scheduled_job group by job_url having count(*)>1) t group by combo order by 2 desc limit 15",
    "sj_hosts": "select split_part(split_part(job_url,'://',2),'/',1) host, count(*) from scheduled_job group by 1 order by 2 desc limit 20",
    "sj_paths_linkedin": "select regexp_replace(split_part(split_part(job_url,'://',2),'?',1), '[0-9]+', 'N', 'g') pattern, count(*) from scheduled_job group by 1 order by 2 desc limit 15",
    "sj_has_query": "select count(*) filter (where job_url like '%?%') with_query, count(*) from scheduled_job",
    "sj_msgs": "select count(distinct gmail_message_id) msgs, count(*) rows from scheduled_job where source <> 'manual'",
    "sj_dates": "select min(created_at)::date, max(created_at)::date from scheduled_job",
    "sj_ready_unpromoted": "select status, hold, count(*) from scheduled_job where promote_after <= now() and status in ('SCHEDULED','APPROVED') group by 1,2",
    "sj_promoted_run_status": "select r.status, count(*) from scheduled_job s join application_run r on r.id = s.promoted_run_id group by 1",
    "sj_promoted_run_errors": "select left(coalesce(r.error_message,''),60) err, count(*) from scheduled_job s join application_run r on r.id = s.promoted_run_id where r.status='FAILED' group by 1 order by 2 desc limit 12",
    "sj_promoted_hosts": "select split_part(split_part(s.job_url,'://',2),'/',1) host, r.status, count(*) from scheduled_job s join application_run r on r.id = s.promoted_run_id group by 1,2 order by 3 desc limit 20",
    "sj_vs_posting_dupe": "select count(*) from scheduled_job s where s.status in ('SCHEDULED','APPROVED') and exists (select 1 from job_posting p join application_run r on r.job_posting_id=p.id where (p.url=s.job_url or p.canonical_url=s.job_url) and r.status='COMPLETED')",
    "runs_by_status": "select status, count(*) from application_run group by 1 order by 2 desc",
    "runs_hosts": "select split_part(split_part(job_url,'://',2),'/',1) host, status, count(*) from application_run group by 1,2 order by 3 desc limit 25",
    "runs_pending_old": "select count(*) from application_run where status='PENDING' and created_at < now() - interval '1 day'",
    "postings_multi_runs": "select n_runs, count(*) from (select job_posting_id, count(*) n_runs from application_run group by 1) t group by 1 order by 1",
    "postings_multi_completed": "select count(*) from (select job_posting_id from application_run where status='COMPLETED' and not is_test_run group by 1 having count(*)>1) t",
    "alias_dupes": "select count(*) from (select account_email from application_run where account_email is not null group by 1 having count(*)>1) t",
    "alias_lengths": "select length(split_part(split_part(account_email,'+job-',2),'@',1)) taglen, count(*) from application_run where account_email like '%+job-%' group by 1 order by 1",
    "asu_by_type": "select status_type, count(*) from application_status_update group by 1 order by 2 desc",
    "asu_run_status": "select r.status, (r.submit_attempted_at is not null) submitted, count(*) from application_status_update u join application_run r on r.id=u.run_id group by 1,2 order by 3 desc",
    "asu_per_run": "select n, count(*) from (select run_id, count(*) n from application_status_update group by 1) t group by 1 order by 1",
    "asu_conf": "select width_bucket(confidence,0,1,5) b, count(*) from application_status_update group by 1 order by 1",
    "asu_sender_vs_alias": "select (u.sender ilike '%greenhouse%') gh_sender, count(*) from application_status_update u group by 1",
    "runs_email_confirmed": "select count(*) filter (where email_confirmed_at is not null) confirmed, count(*) filter (where status='COMPLETED') completed from application_run",
    "match_score_runs": "select count(*) filter (where match_score is not null) scored, count(*) from application_run",
    "sj_promoted_vs_run_score": "select count(*) filter (where s.match_score is not null and r.match_score is not null) both_scored, count(*) filter (where s.match_score = r.match_score) equal, round(avg(abs(s.match_score - r.match_score))) mean_abs_diff, max(abs(s.match_score - r.match_score)) max_diff from scheduled_job s join application_run r on r.id = s.promoted_run_id",
}


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:
        await conn.execute(text("set transaction read only"))
        for name, q in QUERIES.items():
            try:
                rows = (await conn.execute(text(q))).all()
                print(f"== {name}")
                for r in rows:
                    print("  ", tuple(r))
            except Exception as exc:
                print(f"== {name} ERROR {type(exc).__name__}: {str(exc)[:200]}")
                await conn.rollback()
                await conn.execute(text("set transaction read only"))
    await engine.dispose()


asyncio.run(main())
