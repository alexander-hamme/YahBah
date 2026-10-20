import asyncio, logging
logging.disable(logging.CRITICAL)
from sqlalchemy import text
from yahbah.db.session import AsyncSessionLocal

Q = {
 "fk_on_scheduled_job": """select conname, confdeltype from pg_constraint where conrelid='scheduled_job'::regclass and contype='f'""",
 "fks_to_application_run": """select conrelid::regclass, conname, confdeltype from pg_constraint where confrelid='application_run'::regclass and contype='f'""",
 "runs_referenced_by_scheduled": """select r.status, count(*) from application_run r join scheduled_job s on s.promoted_run_id=r.id group by r.status""",
 "runs_with_status_updates": """select count(distinct run_id) from application_status_update""",
 "submit_attempted_by_status": """select status, count(*) from application_run where submit_attempted_at is not null group by status""",
 "test_runs_by_status": """select status, count(*) from application_run where is_test_run group by status""",
 "current_state_values": """select status, current_state, count(*) from application_run group by 1,2 order by 3 desc limit 25""",
 "email_confirmed_by_status": """select status, count(*) from application_run where email_confirmed_at is not null group by status""",
 "artifact_types": """select artifact_type, count(*) from application_artifact group by 1 order by 2 desc""",
 "screenshot_steps": """select artifact_metadata->>'step', count(*) from application_artifact where artifact_type='screenshot' group by 1 order by 2 desc""",
 "abs_paths": """select (path like '/%') as absolute, count(*) from application_artifact group by 1""",
 "scheduled_sources": """select source, status, count(*) from scheduled_job group by 1,2""",
 "confirmation_html_with_handlers": """select count(*) filter (where confirmation_html ~* 'on[a-z]+\\s*='), count(*) filter (where confirmation_html ~* '<(script|iframe|img|svg)'), count(*) filter (where confirmation_html is not null) from application_run""",
 "profiles": """select count(*) from applicant_profile""",
 "answer_cache": """select count(*) from answer_cache""",
}

async def main():
    async with AsyncSessionLocal() as s:
        for k, q in Q.items():
            try:
                print(k, (await s.execute(text(q))).all())
            except Exception as e:
                print(k, "ERR", type(e).__name__, str(e).splitlines()[0][:200])
                await s.rollback()

asyncio.run(main())
