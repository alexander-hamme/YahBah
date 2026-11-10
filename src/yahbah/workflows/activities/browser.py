"""
Browser activities — thin Temporal wrappers over yahbah.agent.

  discover_activity        open the job URL, reach the application form, read job info, create the email alias
  map_form_activity        list every form field, each tagged with a data-ja-field key
  fill_activity            fill every field by key (code first, fix agent last), save the fill evidence and a
                           full-page screenshot; a review re-fill fills exactly the approved answers
  submit_activity          claim the submission, gated submit + verification loop, confirmation artifacts
  close_session_activity   free the run's browser

Browser state lives in SessionRegistry, keyed by run_id and owned by the current activity attempt
(a superseded attempt gets no browser: ApplicationError "superseded"). If the session is gone
(worker restart, or the browser was closed for review), the activity re-opens the job URL,
re-discovers the form and checks it still has the same fields.

Submission safety (contract §6): the submit click happens only after claim_submission() succeeds;
if an agent may have submitted (StageFailed "possibly_submitted") the activity records the
attempt and claim first (record_possible_submission). Either way the run then ends
SUBMIT_UNCERTAIN unless success was verified.
"""
import json
import os
import uuid
from dataclasses import asdict

from browser_use.browser import BrowserSession
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from yahbah.agent import boards
from yahbah.agent.boards import BoardProfile, detect_board, record_observation
from yahbah.agent.discover import StageFailed, Trace, beat, discover, map_form
from yahbah.agent.fill import FillContext, fill_form, read_values, submitted_values
from yahbah.agent.session import SessionRegistry, SessionSuperseded, artifact_path
from yahbah.agent.submit import submit_and_verify
from yahbah.agent.tools import get_form_schema, run_js
from yahbah.credentials import generate_email_alias
from yahbah.db.models import ApplicantProfile, ApplicationRun
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.answers import AnswerEngine, JobContext, NeedsAnswer
from yahbah.llm.logs import use_run_log_dir
from yahbah.run_state import attempt_tag, claim_submission, record_possible_submission
from yahbah.schemas import (
    DiscoverInput,
    DiscoverOutput,
    FillInput,
    FillOutput,
    FormField,
    FormSchema,
    MapFormInput,
    MapFormOutput,
    SubmitInput,
    SubmitOutput,
)
from yahbah.url_utils import canonicalize_url
from yahbah.workflows.activities.db_ops import (
    fenced_update,
    persist_artifact_activity,
    store_credentials_activity,
    superseded,
)
from yahbah.workflows.activities.heartbeat import heartbeating
from yahbah.workflows.activities.llm import (
    field_from_row,
    job_context,
    load_catalog,
    needs_answer_error,
    persist_answer_plan,
)

# Failures that a retry can't fix: fail the run right away with the reason.
_FINAL = {"job_closed", "login_wall", "captcha", "blocked", "needs_answer", "form_changed", "possibly_submitted",
          "submit_button_not_found", "claim_denied", "email_code_entry", "email_code_timeout", "submit_unverified"}


def _app_error(exc: StageFailed | NeedsAnswer) -> ApplicationError:
    if isinstance(exc, NeedsAnswer):
        return needs_answer_error(exc)
    return ApplicationError(str(exc), type=exc.reason, non_retryable=exc.reason in _FINAL)


async def _run_row(run_id: str) -> ApplicationRun:
    async with AsyncSessionLocal() as db:
        run = await db.get(ApplicationRun, uuid.UUID(run_id))
    if run is None:
        raise superseded(run_id)
    return run


async def _stage_failed(run_id: str, exc: StageFailed | NeedsAnswer) -> ApplicationError:
    """The activity's error for a stage failure. An agent that may have submitted the form
    (possibly_submitted) first gets the run marked attempted and the posting claimed (contract §6).
    That error is non-retryable even if recording fails: the terminal write records it again."""
    if isinstance(exc, StageFailed) and exc.reason == "possibly_submitted":
        try:
            run = await _run_row(run_id)
            await record_possible_submission(run_id, activity.info().workflow_id, run.job_posting_id)
        except Exception as record_exc:
            activity.logger.error(f"[tripwire] run {run_id}: recording the possible submission failed "
                                  f"({type(record_exc).__name__}: {record_exc}); the run's ending records it")
    return _app_error(exc)


async def _load_profile() -> ApplicantProfile:
    async with AsyncSessionLocal() as session:
        profile = (await session.execute(select(ApplicantProfile).limit(1))).scalar_one_or_none()
        if profile is None:
            raise RuntimeError("No ApplicantProfile found. Run scripts/seed_profile.py first.")
        return profile


async def _screenshot(run_id: str, label: str, **metadata) -> None:
    """Best effort: a screenshot that can't be taken or registered never hides the stage's own outcome."""
    try:
        if path := await SessionRegistry.instance().screenshot(run_id, label):
            await persist_artifact_activity(run_id, "screenshot", path, {"step": label, **metadata})
    except Exception as exc:
        activity.logger.warning(f"[screenshot] {label!r} for run {run_id} not saved: {exc}")


def _save_trace(run_id: str, trace: Trace) -> list[dict]:
    """Append this activity's stage rows to artifacts/<run_id>/trace.jsonl; return all rows so far."""
    path = artifact_path(run_id, "trace.jsonl")
    with open(path, "a") as f:
        for row in trace.rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


async def _write_json_artifact(run_id: str, artifact_type: str, filename: str, data, metadata=None) -> None:
    path = artifact_path(run_id, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    await persist_artifact_activity(run_id, artifact_type, path, metadata)


def _schema_from_dict(raw: dict) -> FormSchema:
    return FormSchema(
        fields=[FormField(**f) for f in raw.get("fields", [])],
        page_url=raw.get("page_url", ""),
        board=raw.get("board", "generic"),
    )


async def _detect(session: BrowserSession) -> BoardProfile:
    return await detect_board(lambda c: run_js(session, c), await session.get_current_page_url())


async def _session_with_form(run_id: str, job_url: str, schema: FormSchema | None, trace: Trace, *,
                             headed: bool) -> BrowserSession:
    """The run's live session, or a fresh one re-discovered up to the form. With a schema, the
    re-read form must have the same keys and labels, or the stored mapping can't be trusted.
    A run that discover moved to the headed fallback re-opens headed; the re-discover never
    falls back itself, so the browser mode only changes where the workflow learns of it (contract §2)."""
    registry = SessionRegistry.instance()
    if session := registry.get(run_id):
        return session
    activity.logger.warning(f"[session] no live browser for run {run_id} — re-opening {job_url}")
    try:
        session = await registry.open(run_id, job_url, headed=headed)
        await discover(session, trace)  # (never falls back, so never swaps; guarded all the same)
    except SessionSuperseded as exc:
        raise superseded(run_id) from exc
    if schema is not None:
        now = {f["key"]: f["label"] for f in await get_form_schema(session)}
        changed = [f.label for f in schema.fields if now.get(f.key) != f.label]
        if changed:
            raise StageFailed("form_changed", f"re-opened form differs from the mapped one: {changed[:5]}")
    return session


def _map_new_fields(run_id: str, review_round: int, profile: ApplicantProfile, ctx: FillContext, job: JobContext,
                    account_email: str | None):
    """Answers for fields that appear while filling, and re-answers for planned values that matched no option
    (rows with `feedback`): the same answer engine as MAP_ANSWERS, the form's other answers as prior context."""
    async def map_new(rows: list[dict]) -> list[dict]:
        keys = {r["key"] for r in rows}
        feedback = {r["key"]: r["feedback"] for r in rows if r.get("feedback")}
        prior = [m for k, m in ctx.mappings.items() if k not in keys]
        plan = await AnswerEngine().plan([field_from_row(r) for r in rows], job,
                                         load_catalog(profile, account_email), prior=prior, feedback=feedback)
        await persist_answer_plan(run_id, plan, "fill", review_round)
        plan.raise_if_blocked()
        return [asdict(m) for m in plan.mappings]
    return map_new


def _fill_context(schema: FormSchema, mappings: list[dict], profile: ApplicantProfile,
                  cover_letter_path: str | None, cover_letter_text: str | None) -> FillContext:
    files = {"resume": profile.resume_path}
    if cover_letter_path:
        files["cover_letter"] = cover_letter_path
    return FillContext(
        fields={f.key: {"label": f.label, "type": f.field_type, "required": f.required, "options": f.options,
                        "empty_value": f.empty_value, "description": f.description, "section": f.section}
                for f in schema.fields},
        mappings={m["key"]: m for m in mappings},
        files=files,
        cover_letter_text=cover_letter_text,
    )


def _schema_with_ctx_fields(schema: FormSchema, ctx: FillContext) -> dict:
    """The schema plus fields that appeared while filling."""
    known = {f.key for f in schema.fields}
    extra = [FormField(key=k, label=f["label"], field_type=f["type"], required=f["required"], options=f["options"],
                       empty_value=f.get("empty_value"), description=f.get("description", ""),
                       section=f.get("section", ""))
             for k, f in ctx.fields.items() if k not in known]
    return asdict(FormSchema(fields=schema.fields + extra, page_url=schema.page_url, board=schema.board))


def _required_unmapped(ctx: FillContext) -> list[dict]:
    return [f for f in ctx.unmapped_new_fields if f.get("required")]


# ── Activities ────────────────────────────────────────────────────────────────

@activity.defn
@heartbeating
async def discover_activity(input: DiscoverInput) -> DiscoverOutput:
    use_run_log_dir(input.run_id)
    trace = Trace()
    try:
        session = await SessionRegistry.instance().open(input.run_id, input.job_url)
    except SessionSuperseded as exc:
        raise superseded(input.run_id) from exc
    try:
        result = await discover(session, trace, headed_fallback=input.headed_fallback)
    except SessionSuperseded as exc:  # from the headed swap: the run is a newer attempt's, record nothing
        raise superseded(input.run_id) from exc
    except StageFailed as exc:
        await _screenshot(input.run_id, "discover_failed")
        _save_trace(input.run_id, trace)
        raise await _stage_failed(input.run_id, exc) from exc
    await _screenshot(input.run_id, "form_found", headed=result.headed)
    _save_trace(input.run_id, trace)

    # A trackable alias per application: company replies are matched back to this run by it.
    profile = await _load_profile()
    email = await generate_email_alias(profile.email, input.job_url)
    await store_credentials_activity(input.run_id, email, None)

    job = result.job
    activity.logger.info(
        f"[discover] board={result.board.name} page={result.page_url} headed={result.headed} "
        f"job info from {job.get('source')}: "
        f"{job.get('job_title')!r} @ {job.get('company')!r}"
    )
    return DiscoverOutput(
        page_url=result.page_url,
        board=result.board.name,
        account_email=email,
        job_description=job.get("description") or "",
        canonical_url=canonicalize_url(job.get("url") or result.page_url),
        job_title=job.get("job_title") or None,
        job_company=job.get("company") or None,
        job_location=job.get("location") or None,
        posted_date=job.get("posted_date") or None,
        headed=result.headed,
    )


@activity.defn
@heartbeating
async def map_form_activity(input: MapFormInput) -> MapFormOutput:
    use_run_log_dir(input.run_id)
    trace = Trace()
    try:
        session = await _session_with_form(input.run_id, input.job_url, None, trace, headed=input.headed)
        board = await _detect(session)
        fields = await map_form(session, board, trace)
    except StageFailed as exc:
        await _screenshot(input.run_id, "map_failed")
        _save_trace(input.run_id, trace)
        raise await _stage_failed(input.run_id, exc) from exc

    schema = FormSchema(
        fields=[FormField(key=f["key"], label=f["label"], field_type=f["type"], required=f["required"],
                          options=f["options"], options_total=f.get("options_total"),
                          empty_value=f.get("empty_value"), autocomplete=f.get("autocomplete")) for f in fields],
        page_url=await session.get_current_page_url(),
        board=board.name,
    )
    activity.logger.info(
        f"[map] {len(fields)} fields: " + ", ".join(f"{f['label']!r}({f['type']})" for f in fields))
    await _write_json_artifact(input.run_id, "form_schema", "form_schema.json", asdict(schema))
    record_observation(input.run_id, input.job_url, board, _save_trace(input.run_id, trace), len(fields))
    return MapFormOutput(form_schema_dict=asdict(schema))


@activity.defn
@heartbeating
async def fill_activity(input: FillInput) -> FillOutput:
    """Fill the form and save its evidence for review round input.review_round (contract §4, §8):
    the submitted_values rows and a full-page screenshot.

    First fill: fields that appear while filling are mapped from the profile; the browser is closed
    afterwards (the run goes to review, or ends in testing mode). Re-fill (input.refill, after an
    approval): exactly the approved mappings; a field without one is returned in
    unmapped_new_fields; the browser stays open for SUBMIT unless a required one appeared.
    """
    use_run_log_dir(input.run_id)
    trace = Trace()
    schema = _schema_from_dict(input.form_schema_dict)
    board = boards.by_name(schema.board)
    profile = await _load_profile()
    ctx = _fill_context(schema, input.field_mappings, profile, input.cover_letter_path, input.cover_letter_text)
    map_new = None if input.refill else _map_new_fields(input.run_id, input.review_round, profile, ctx,
                                                         job_context(input), input.account_email)
    registry = SessionRegistry.instance()
    try:
        session = await _session_with_form(input.run_id, input.job_url, schema, trace, headed=input.headed)
        problems = await fill_form(session, board, ctx, map_new_fields=map_new, beat=beat)
    except (StageFailed, NeedsAnswer) as exc:
        await _screenshot(input.run_id, "fill_failed")
        raise await _stage_failed(input.run_id, exc) from exc
    finally:
        _save_trace(input.run_id, trace)

    # Per attempt: an attempt that lost the lease can't overwrite (or register over) its retry's evidence.
    evidence = {"review_round": input.review_round, "headed": input.headed}  # (contract §2: the user sees it)
    tag = f"{input.review_round}_{attempt_tag()}"
    values = await read_values(session, list(ctx.fields))
    await _write_json_artifact(input.run_id, "submitted_values", f"submitted_values_{tag}.json",
                               submitted_values(ctx, values), evidence)
    await _screenshot(input.run_id, f"filled_{tag}", **evidence)
    output = FillOutput(
        form_schema_dict=_schema_with_ctx_fields(schema, ctx),
        field_mappings=list(ctx.mappings.values()),
        written=ctx.written,
        unmapped_new_fields=list(ctx.unmapped_new_fields),
        baseline=ctx.baseline,
    )
    if input.refill and _required_unmapped(ctx):
        activity.logger.info(f"[fill] {len(_required_unmapped(ctx))} required field(s) without an approved "
                             f"answer: back to review")
        await registry.close(input.run_id)
        return output
    if problems:
        raise _app_error(StageFailed("fill_failed", "; ".join(problems)[:1000]))
    if not input.refill:
        await registry.close(input.run_id)
    return output


@activity.defn
@heartbeating
async def submit_activity(input: SubmitInput) -> SubmitOutput:
    use_run_log_dir(input.run_id)
    workflow_id = activity.info().workflow_id
    schema = _schema_from_dict(input.form_schema_dict)
    board = boards.by_name(schema.board)
    profile = await _load_profile()
    ctx = _fill_context(schema, input.field_mappings, profile, input.cover_letter_path, input.cover_letter_text)
    ctx.written = dict(input.written)
    ctx.baseline = input.baseline  # the approved re-fill's (a re-fill below takes its own)

    run = await _run_row(input.run_id)
    already = run.submit_attempted_at is not None

    registry = SessionRegistry.instance()
    session = registry.get(input.run_id)
    if session is None:
        if already:
            # Never re-submit blind: the earlier attempt may have gone through.
            raise ApplicationError(
                "submit_unverified: the browser was lost after clicking submit; "
                "the confirmation email (if any) will mark this run as verified",
                type="submit_unverified", non_retryable=True)
        # Lost between the approved re-fill and submit (worker restart): re-open and re-fill the same
        # answers, as fill_activity's re-fill does (from the mapped form, so it can be checked).
        trace = Trace()
        mapped = _schema_from_dict(input.mapped_form_dict or input.form_schema_dict)
        ctx = _fill_context(mapped, input.field_mappings, profile, input.cover_letter_path, input.cover_letter_text)
        try:
            session = await _session_with_form(input.run_id, input.job_url, mapped, trace, headed=input.headed)
            problems = await fill_form(session, board, ctx, map_new_fields=None, beat=beat)
        except (StageFailed, NeedsAnswer) as exc:
            raise await _stage_failed(input.run_id, exc) from exc
        finally:
            _save_trace(input.run_id, trace)
        if new := _required_unmapped(ctx):
            raise _app_error(StageFailed("form_changed", "required field(s) without an approved answer appeared "
                                                         f"on re-fill: {[f['label'] for f in new][:5]}"))
        if problems:
            raise _app_error(StageFailed("fill_failed", "; ".join(problems)[:1000]))

    evidence_path = artifact_path(input.run_id, "submit_evidence.jsonl")
    evidence = {"registered": False}

    async def register_evidence() -> None:
        """Registers the evidence file as a run artifact once; retried until it succeeds."""
        if not evidence["registered"] and os.path.exists(evidence_path):
            await persist_artifact_activity(input.run_id, "submit_evidence", evidence_path, {"headed": input.headed})
            evidence["registered"] = True

    async def record(round_evidence: dict) -> None:
        """Each verify round's evidence, appended as it arrives so it survives a lost worker."""
        with open(evidence_path, "a") as f:
            f.write(json.dumps(round_evidence, ensure_ascii=False, default=str) + "\n")
        await register_evidence()

    try:
        result = await submit_and_verify(
            session, board, ctx,
            email=input.account_email,
            already_attempted=already,
            claim=lambda: claim_submission(input.run_id, workflow_id, run.job_posting_id),
            record=record,
            map_new_fields=None,  # after the claim nothing new is mapped (contract §4)
            beat=beat,
        )
    except (StageFailed, NeedsAnswer) as exc:
        await _screenshot(input.run_id, "submit_failed")
        # Never retried: once submit may have been clicked the run ends SUBMIT_UNCERTAIN (its claim is recorded).
        err = await _stage_failed(input.run_id, exc)
        raise ApplicationError(err.message, type=err.type, non_retryable=True) from exc
    finally:
        try:
            await register_evidence()  # (if every registration during the rounds failed)
        except Exception as exc:
            activity.logger.error(f"[submit] run {input.run_id}: submit evidence not registered: {exc}")

    await _screenshot(input.run_id, "confirmation")
    for artifact_type, filename, content in (("confirmation", "confirmation.txt", result.confirmation_text),
                                             ("confirmation_html", "confirmation.html", result.confirmation_html)):
        if content:
            path = artifact_path(input.run_id, filename)
            with open(path, "w") as f:
                f.write(content)
            await persist_artifact_activity(input.run_id, artifact_type, path)
    if result.confirmation_html:
        await fenced_update(input.run_id, confirmation_html=result.confirmation_html)

    await registry.close(input.run_id)
    return SubmitOutput(confirmation_url=result.confirmation_url, confirmation_text=result.confirmation_text)


@activity.defn
async def close_session_activity(run_id: str) -> None:
    await SessionRegistry.instance().close(run_id)
