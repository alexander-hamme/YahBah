"""
Run the agentic pipeline on one job URL without Temporal or a run record — for debugging a board.

  uv run python scripts/run_pipeline.py <job-url>                      # discover → map form → answers → fill
  uv run python scripts/run_pipeline.py <job-url> --force-tier oneshot # start form mapping at a fallback tier
  uv run python scripts/run_pipeline.py <job-url> --cover-letter       # also generate + attach a cover letter
  uv run python scripts/run_pipeline.py <job-url> --headed --keep-open # watch it, leave the browser open
  uv run python scripts/run_pipeline.py <job-url> --headed-fallback    # a page walled off headless: retry it headed
  uv run python scripts/run_pipeline.py <job-url> --submit             # really submit (gated + verified)

Never submits without --submit. Uses the applicant profile from the DB (read-only), the facts in
config/personal.yaml and the profile's real email (no per-run alias). Artifacts (answer_plan*.json
included) and LLM logs: artifacts/debug-<timestamp>/.
"""
import argparse
import asyncio
import json
import time
from dataclasses import asdict

import yahbah.agent  # noqa: F401  (sets browser-use env vars before it is imported)
from sqlalchemy import select

from yahbah.agent.boards import detect_board, record_observation
from yahbah.agent.discover import StageFailed, Trace, discover, map_form
from yahbah.agent.fill import FillContext, fill_form, read_values, submitted_values
from yahbah.agent.session import SessionRegistry, artifact_path
from yahbah.agent.submit import submit_and_verify
from yahbah.agent.tools import run_js
from yahbah.config import settings
from yahbah.db.models import ApplicantProfile
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.answers import AnswerEngine, AnswerPlan, JobContext, NeedsAnswer
from yahbah.llm.cover_letter import CoverLetterGenerator
from yahbah.llm.facts import FactsConfigError, load_facts
from yahbah.llm.logs import use_run_log_dir
from yahbah.workflows.activities.llm import _text_to_pdf, field_from_row


def _save_plan(run_id: str, plan: AnswerPlan, name: str, stage: str) -> None:
    with open(artifact_path(run_id, name), "w") as f:
        json.dump(plan.to_artifact(stage, None), f, indent=2, ensure_ascii=False)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--force-tier", choices=["schema", "oneshot", "agent"], default="schema")
    ap.add_argument("--cover-letter", action="store_true", help="generate a cover letter (prose model)")
    ap.add_argument("--submit", action="store_true", help="really submit the application")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep-open", action="store_true", help="leave the browser open at the end")
    ap.add_argument("--headed-fallback", action="store_true",
                    help="reopen a page that walls off the headless browser once in a headed window (contract §2)")
    args = ap.parse_args()

    if args.headed:
        settings.browser_headless = False
    run_id = f"debug-{time.strftime('%Y%m%d-%H%M%S')}"
    print(f"artifacts + LLM logs: {artifact_path(run_id, '')}")
    use_run_log_dir(run_id)
    trace = Trace()
    started = time.time()
    registry = SessionRegistry.instance()
    session = await registry.open(run_id, args.url)
    try:
        result = await discover(session, trace, headed_fallback=args.headed_fallback)
        session = registry.get(run_id)  # the headed fallback swaps the run's browser
        if result.headed:
            print("\nheaded fallback: the run continues in a headed browser")
        board = await detect_board(lambda c: run_js(session, c), await session.get_current_page_url())
        job = result.job
        print(f"\nboard: {board.name}   job info ({job.get('source')}): "
              f"{job.get('job_title')!r} @ {job.get('company')!r}")

        fields = await map_form(session, board, trace, args.force_tier)
        print(f"\n=== form: {len(fields)} fields ===")
        for f in fields:
            more = f" (+{f['options_total'] - len(f['options'])})" if (f.get("options_total") or 0) > len(f["options"]) else ""
            print(f"  [{f['key']:>5}] {'*' if f['required'] else ' '} {f['label'][:60]!r:62} {f['type']:9} "
                  f"{f['options'][:4]}{more}")

        async with AsyncSessionLocal() as db:
            profile = (await db.execute(select(ApplicantProfile).limit(1))).scalar_one()
        catalog = load_facts(profile)
        job_context = JobContext(location=job.get("location") or None, description=job.get("description") or "",
                                 company=job.get("company") or None, title=job.get("job_title") or None)
        t = time.time()
        plan = await AnswerEngine().plan([field_from_row(f) for f in fields], job_context, catalog)
        _save_plan(run_id, plan, "answer_plan.json", "map_answers")
        trace.add("answers", "model", t, f"{len(plan.mappings)} fields, {len(plan.calls)} model calls")
        print("\n=== answers ===")
        for m in plan.mappings:
            print(f"  [{m.key:>5}] {m.label[:50]!r:52} <- {m.source:12} {m.value[:70]!r} {m.basis}")
        plan.raise_if_blocked()

        files = {"resume": profile.resume_path}
        cover_text = None
        if args.cover_letter:
            t = time.time()
            cover_text = await CoverLetterGenerator().generate(job.get("description") or "", profile)
            files["cover_letter"] = artifact_path(run_id, "cover_letter.pdf")
            _text_to_pdf(cover_text, files["cover_letter"])
            trace.add("cover_letter", "model", t, f"{len(cover_text)} chars")

        ctx = FillContext(
            fields={f["key"]: {"label": f["label"], "type": f["type"], "required": f["required"],
                               "options": f["options"], "empty_value": f.get("empty_value"),
                               "description": f.get("description") or "", "section": f.get("section") or ""}
                    for f in fields},
            mappings={m.key: asdict(m) for m in plan.mappings},
            files=files,
            cover_letter_text=cover_text,
        )
        fill_plans = 0

        async def map_new(rows: list[dict]) -> list[dict]:
            nonlocal fill_plans
            keys = {r["key"] for r in rows}
            new_plan = await AnswerEngine().plan(
                [field_from_row(r) for r in rows], job_context, load_facts(profile),
                prior=[m for k, m in ctx.mappings.items() if k not in keys],
                feedback={r["key"]: r["feedback"] for r in rows if r.get("feedback")})
            fill_plans += 1
            _save_plan(run_id, new_plan, f"answer_plan_fill_{fill_plans}.json", "fill")
            new_plan.raise_if_blocked()
            return [asdict(m) for m in new_plan.mappings]

        t = time.time()
        problems = await fill_form(session, board, ctx, map_new_fields=map_new, beat=lambda note: print(f"  · {note}"))
        trace.add("fill", "code", t, f"{len(ctx.written)} written, {len(problems)} problem(s)")
        values = await read_values(session, list(ctx.fields))
        print("\n=== filled ===")
        for row in submitted_values(ctx, values):
            mark = "ok " if row["ok"] else "BAD"
            print(f"  {mark} [{row['key']:>5}] {row['label'][:45]!r:47} wrote {str(row['written'])[:35]!r:37} "
                  f"page {str(row['page_value'])[:35]!r}")
        for p in problems:
            print(f"  PROBLEM: {p}")
        await registry.screenshot(run_id, "filled")

        if args.submit:
            if problems:
                print("\nnot submitting: problems left")
            else:
                async def allow() -> bool:
                    return True
                result = await submit_and_verify(session, board, ctx, email=profile.email, already_attempted=False,
                                                 claim=allow, map_new_fields=map_new,
                                                 beat=lambda note: print(f"  · {note}"))
                print(f"\nSUBMITTED: {result.confirmation_url}\n{result.confirmation_text[:500]}")
                await registry.screenshot(run_id, "confirmation")

        record_observation(run_id, args.url, board, trace.rows, len(fields))
    except NeedsAnswer as exc:
        print("\nFAILED needs_answer:")
        for u in exc.unresolved:
            print(f"  {u.label} ({u.status}): {u.reason}")
    except FactsConfigError as exc:
        print(f"\nFAILED: {exc}")
    except StageFailed as exc:
        print(f"\nFAILED {exc.reason}: {exc.detail}")
        await registry.screenshot(run_id, "failed")
    finally:
        print(f"\n=== trace ({time.time() - started:.0f}s total) ===")
        for row in trace.rows:
            print(f"  {row['stage']:12} {row['method']:5} {row['secs']:6.1f}s  {row['note']}")
        if args.keep_open:
            input("\nbrowser left open — press Enter to close it ")
        await registry.close(run_id)


if __name__ == "__main__":
    asyncio.run(main())
