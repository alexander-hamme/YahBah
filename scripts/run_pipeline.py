"""
Run the agentic pipeline on one job URL without Temporal or a run record — for debugging a board.

  uv run python scripts/run_pipeline.py <job-url>                      # discover → map form → answers → fill
  uv run python scripts/run_pipeline.py <job-url> --force-tier oneshot # start form mapping at a fallback tier
  uv run python scripts/run_pipeline.py <job-url> --cover-letter       # also generate + attach a cover letter
  uv run python scripts/run_pipeline.py <job-url> --headed --keep-open # watch it, leave the browser open
  uv run python scripts/run_pipeline.py <job-url> --submit             # really submit (gated + verified)

Never submits without --submit. Uses the applicant profile from the DB (read-only) and its real
email (no per-run alias). Artifacts and LLM logs: artifacts/debug-<timestamp>/.
"""
import argparse
import asyncio
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
from yahbah.llm.cover_letter import CoverLetterGenerator
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer
from yahbah.llm.logs import use_run_log_dir
from yahbah.schemas import FieldMapping, FormField
from yahbah.workflows.activities.llm import _text_to_pdf


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--force-tier", choices=["schema", "oneshot", "agent"], default="schema")
    ap.add_argument("--cover-letter", action="store_true", help="generate a cover letter (prose model)")
    ap.add_argument("--submit", action="store_true", help="really submit the application")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep-open", action="store_true", help="leave the browser open at the end")
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
        result = await discover(session, trace)
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
        form_fields = [FormField(key=f["key"], label=f["label"], field_type=f["type"], required=f["required"],
                                 options=f["options"], options_total=f.get("options_total"),
                                 empty_value=f.get("empty_value"), autocomplete=f.get("autocomplete"))
                       for f in fields]
        t = time.time()
        mappings = await FieldMapper().map(form_fields, profile, job.get("description") or "")
        trace.add("answers", "model", t, f"{len(mappings)} fields mapped")
        print("\n=== answers ===")
        for m in mappings:
            print(f"  [{m.key:>5}] {m.label[:50]!r:52} <- {m.source:20} {m.value[:70]!r}")

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
                               "options": f["options"], "empty_value": f.get("empty_value")} for f in fields},
            mappings={m.key: asdict(m) for m in mappings},
            files=files,
            cover_letter_text=cover_text,
        )

        async def map_new(new_fields: list[dict]) -> list[dict]:
            new = [FormField(key=f["key"], label=f["label"], field_type=f["type"], required=bool(f["required"]),
                             options=f.get("options") or [], autocomplete=f.get("autocomplete")) for f in new_fields]
            prior = [FieldMapping(**m) for m in ctx.mappings.values()]
            return [asdict(m) for m in await FieldMapper().map(new, profile, job.get("description") or "", prior)]

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
                async def noop() -> None:
                    pass
                result = await submit_and_verify(session, board, ctx, email=profile.email, already_attempted=False,
                                                 mark_attempted=noop, map_new_fields=map_new,
                                                 beat=lambda note: print(f"  · {note}"))
                print(f"\nSUBMITTED: {result.confirmation_url}\n{result.confirmation_text[:500]}")
                await registry.screenshot(run_id, "confirmation")

        record_observation(run_id, args.url, board, trace.rows, len(fields))
    except NeedsAnswer as exc:
        print(f"\nFAILED needs_answer: {exc.label!r}")
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
