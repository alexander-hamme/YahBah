"""
Browser activities — thin Temporal wrappers over yahbah.agent.

  discover_activity        open the job URL, reach the application form, read job info, create the email alias
  map_form_activity        list every form field, each tagged with a data-ja-field key
  fill_activity            fill every field by key (code first, fix agent last); stops here in testing mode
  submit_activity          gated submit + verification loop (email codes, fixes), confirmation artifacts
  close_session_activity   free the run's browser (failure paths)

Browser state lives in SessionRegistry, keyed by run_id. If the session is gone (worker restart),
the activity re-opens the job URL, re-discovers the form and checks it still has the same fields.
"""
import json
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
from yahbah.agent.session import SessionRegistry, artifact_path
from yahbah.agent.submit import submit_and_verify
from yahbah.agent.tools import get_form_schema, run_js
from yahbah.config import get_runtime_settings
from yahbah.credentials import generate_email_alias
from yahbah.db.models import ApplicantProfile, ApplicationRun
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer
from yahbah.llm.logs import use_run_log_dir
from yahbah.schemas import (
    DiscoverInput,
    DiscoverOutput,
    FieldMapping,
    FillInput,
    FillOutput,
    FormField,
    FormSchema,
    MapFormInput,
    MapFormOutput,
    SubmitInput,
    SubmitOutput,
)
from yahbah.url_utils import ats_normalize, strip_tracking
from yahbah.workflows.activities.db_ops import (
    mark_submit_attempted,
    persist_artifact_activity,
    store_credentials_activity,
)

# Failures that a retry can't fix: fail the run right away with the reason.
_FINAL = {"job_closed", "login_wall", "captcha", "blocked", "needs_answer", "form_changed",
          "submit_button_not_found", "email_code_entry", "email_code_timeout", "submit_unverified"}


def _app_error(exc: StageFailed | NeedsAnswer) -> ApplicationError:
    if isinstance(exc, NeedsAnswer):
        return ApplicationError(str(exc), type="needs_answer", non_retryable=True)
    return ApplicationError(str(exc), type=exc.reason, non_retryable=exc.reason in _FINAL)


async def _load_profile() -> ApplicantProfile:
    async with AsyncSessionLocal() as session:
        profile = (await session.execute(select(ApplicantProfile).limit(1))).scalar_one_or_none()
        if profile is None:
            raise RuntimeError("No ApplicantProfile found. Run scripts/seed_profile.py first.")
        return profile


async def _screenshot(run_id: str, label: str) -> None:
    if path := await SessionRegistry.instance().screenshot(run_id, label):
        await persist_artifact_activity(run_id, "screenshot", path, {"step": label})


def _save_trace(run_id: str, trace: Trace) -> list[dict]:
    """Append this activity's stage rows to artifacts/<run_id>/trace.jsonl; return all rows so far."""
    path = artifact_path(run_id, "trace.jsonl")
    with open(path, "a") as f:
        for row in trace.rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


async def _write_json_artifact(run_id: str, artifact_type: str, filename: str, data) -> None:
    path = artifact_path(run_id, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    await persist_artifact_activity(run_id, artifact_type, path)


def _schema_from_dict(raw: dict) -> FormSchema:
    return FormSchema(
        fields=[FormField(**f) for f in raw.get("fields", [])],
        page_url=raw.get("page_url", ""),
        board=raw.get("board", "generic"),
    )


async def _detect(session: BrowserSession) -> BoardProfile:
    return await detect_board(lambda c: run_js(session, c), await session.get_current_page_url())


async def _session_with_form(run_id: str, job_url: str, schema: FormSchema | None, trace: Trace) -> BrowserSession:
    """The run's live session, or a fresh one re-discovered up to the form. With a schema, the
    re-read form must have the same keys and labels, or the stored mapping can't be trusted."""
    registry = SessionRegistry.instance()
    if session := registry.get(run_id):
        return session
    activity.logger.warning(f"[session] no live browser for run {run_id} — re-opening {job_url}")
    session = await registry.open(run_id, job_url)
    await discover(session, trace)
    if schema is not None:
        now = {f["key"]: f["label"] for f in await get_form_schema(session)}
        changed = [f.label for f in schema.fields if now.get(f.key) != f.label]
        if changed:
            raise StageFailed("form_changed", f"re-opened form differs from the mapped one: {changed[:5]}")
    return session


def _map_new_fields(profile: ApplicantProfile, ctx: FillContext, job_description: str, account_email: str | None):
    """Mapper for fields that appear while filling/submitting: same profile-based mapping as the rest."""
    async def map_new(new_fields: list[dict]) -> list[dict]:
        fields = [FormField(key=f["key"], label=f["label"], field_type=f["type"], required=bool(f["required"]),
                            options=f.get("options") or [], autocomplete=f.get("autocomplete")) for f in new_fields]
        prior = [FieldMapping(**m) for m in ctx.mappings.values()]
        mapped = await FieldMapper().map(fields, profile, job_description, prior=prior)
        for m in mapped:
            if m.source == "email" and account_email:
                m.value = account_email
        return [asdict(m) for m in mapped]
    return map_new


def _fill_context(schema: FormSchema, mappings: list[dict], profile: ApplicantProfile,
                  cover_letter_path: str | None, cover_letter_text: str | None) -> FillContext:
    files = {"resume": profile.resume_path}
    if cover_letter_path:
        files["cover_letter"] = cover_letter_path
    return FillContext(
        fields={f.key: {"label": f.label, "type": f.field_type, "required": f.required, "options": f.options,
                        "empty_value": f.empty_value}
                for f in schema.fields},
        mappings={m["key"]: m for m in mappings},
        files=files,
        cover_letter_text=cover_letter_text,
    )


def _schema_with_ctx_fields(schema: FormSchema, ctx: FillContext) -> dict:
    """The schema plus fields that appeared while filling."""
    known = {f.key for f in schema.fields}
    extra = [FormField(key=k, label=f["label"], field_type=f["type"], required=f["required"], options=f["options"],
                       empty_value=f.get("empty_value"))
             for k, f in ctx.fields.items() if k not in known]
    return asdict(FormSchema(fields=schema.fields + extra, page_url=schema.page_url, board=schema.board))


# ── Activities ────────────────────────────────────────────────────────────────

@activity.defn
async def discover_activity(input: DiscoverInput) -> DiscoverOutput:
    use_run_log_dir(input.run_id)
    trace = Trace()
    session = await SessionRegistry.instance().open(input.run_id, input.job_url)
    try:
        result = await discover(session, trace)
    except StageFailed as exc:
        await _screenshot(input.run_id, "discover_failed")
        _save_trace(input.run_id, trace)
        raise _app_error(exc) from exc
    await _screenshot(input.run_id, "form_found")
    _save_trace(input.run_id, trace)

    # A trackable alias per application: company replies are matched back to this run by it.
    profile = await _load_profile()
    email = await generate_email_alias(profile.email, input.job_url)
    await store_credentials_activity(input.run_id, email, None)

    job = result.job
    activity.logger.info(
        f"[discover] board={result.board.name} page={result.page_url} job info from {job.get('source')}: "
        f"{job.get('job_title')!r} @ {job.get('company')!r}"
    )
    return DiscoverOutput(
        page_url=result.page_url,
        board=result.board.name,
        account_email=email,
        job_description=job.get("description") or "",
        canonical_url=ats_normalize(strip_tracking(job.get("url") or result.page_url)),
        job_title=job.get("job_title") or None,
        job_company=job.get("company") or None,
        job_location=job.get("location") or None,
        posted_date=job.get("posted_date") or None,
    )


@activity.defn
async def map_form_activity(input: MapFormInput) -> MapFormOutput:
    use_run_log_dir(input.run_id)
    trace = Trace()
    try:
        session = await _session_with_form(input.run_id, input.job_url, None, trace)
        board = await _detect(session)
        fields = await map_form(session, board, trace)
    except StageFailed as exc:
        await _screenshot(input.run_id, "map_failed")
        _save_trace(input.run_id, trace)
        raise _app_error(exc) from exc

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
async def fill_activity(input: FillInput) -> FillOutput:
    use_run_log_dir(input.run_id)
    trace = Trace()
    schema = _schema_from_dict(input.form_schema_dict)
    board = boards.by_name(schema.board)
    profile = await _load_profile()
    ctx = _fill_context(schema, input.field_mappings, profile, input.cover_letter_path, input.cover_letter_text)
    try:
        session = await _session_with_form(input.run_id, input.job_url, schema, trace)
        await _screenshot(input.run_id, "before_fill")
        problems = await fill_form(
            session, board, ctx,
            map_new_fields=_map_new_fields(profile, ctx, input.job_description, input.account_email),
            beat=beat,
        )
    except NeedsAnswer as exc:
        await _screenshot(input.run_id, "fill_failed")
        raise _app_error(exc) from exc
    except StageFailed as exc:
        await _screenshot(input.run_id, "fill_failed")
        raise _app_error(exc) from exc
    finally:
        _save_trace(input.run_id, trace)

    values = await read_values(session, list(ctx.fields))
    await _write_json_artifact(input.run_id, "submitted_values", "submitted_values.json",
                               submitted_values(ctx, values))
    await _screenshot(input.run_id, "before_submit")
    if problems:
        raise _app_error(StageFailed("fill_failed", "; ".join(problems)[:1000]))

    output = FillOutput(
        testing_mode=False,
        form_schema_dict=_schema_with_ctx_fields(schema, ctx),
        field_mappings=list(ctx.mappings.values()),
        written=ctx.written,
    )
    if (await get_runtime_settings()).get("testing_mode"):
        activity.logger.info("[fill] TESTING MODE — form filled, not submitting")
        async with AsyncSessionLocal() as db:
            if run := await db.get(ApplicationRun, uuid.UUID(input.run_id)):
                run.is_test_run = True
                run.confirmation_html = "<p><strong>Testing mode</strong> — form was filled but not submitted.</p>"
                await db.commit()
        await SessionRegistry.instance().close(input.run_id)
        output.testing_mode = True
    return output


@activity.defn
async def submit_activity(input: SubmitInput) -> SubmitOutput:
    use_run_log_dir(input.run_id)
    schema = _schema_from_dict(input.form_schema_dict)
    board = boards.by_name(schema.board)
    profile = await _load_profile()
    ctx = _fill_context(schema, input.field_mappings, profile, input.cover_letter_path, input.cover_letter_text)
    ctx.written = dict(input.written)

    async with AsyncSessionLocal() as db:
        run = await db.get(ApplicationRun, uuid.UUID(input.run_id))
        already = run is not None and run.submit_attempted_at is not None

    registry = SessionRegistry.instance()
    session = registry.get(input.run_id)
    if session is None:
        if already:
            # Never re-submit blind: the earlier attempt may have gone through.
            raise ApplicationError(
                "submit_unverified: the browser was lost after clicking submit; "
                "the confirmation email (if any) will mark this run as verified",
                type="submit_unverified", non_retryable=True)
        # Lost between fill and submit (worker restart): re-open, re-fill, then submit.
        trace = Trace()
        try:
            session = await _session_with_form(input.run_id, input.job_url, schema, trace)
            ctx.written = {}
            problems = await fill_form(session, board, ctx, beat=beat)
        except StageFailed as exc:
            raise _app_error(exc) from exc
        finally:
            _save_trace(input.run_id, trace)
        if problems:
            raise _app_error(StageFailed("fill_failed", "; ".join(problems)[:1000]))

    try:
        result = await submit_and_verify(
            session, board, ctx,
            email=input.account_email,
            already_attempted=already,
            mark_attempted=lambda: mark_submit_attempted(input.run_id),
            map_new_fields=_map_new_fields(profile, ctx, input.job_description, input.account_email),
            beat=beat,
        )
    except (StageFailed, NeedsAnswer) as exc:
        await _screenshot(input.run_id, "submit_failed")
        # Never retried once submit may have been clicked; the run fails with the reason.
        err = _app_error(exc)
        raise ApplicationError(err.message, type=err.type, non_retryable=True) from exc

    await _screenshot(input.run_id, "confirmation")
    for artifact_type, filename, content in (("confirmation", "confirmation.txt", result.confirmation_text),
                                             ("confirmation_html", "confirmation.html", result.confirmation_html)):
        if content:
            path = artifact_path(input.run_id, filename)
            with open(path, "w") as f:
                f.write(content)
            await persist_artifact_activity(input.run_id, artifact_type, path)
    if result.confirmation_html:
        async with AsyncSessionLocal() as db:
            if run := await db.get(ApplicationRun, uuid.UUID(input.run_id)):
                run.confirmation_html = result.confirmation_html
                await db.commit()

    await registry.close(input.run_id)
    return SubmitOutput(confirmation_url=result.confirmation_url, confirmation_text=result.confirmation_text)


@activity.defn
async def close_session_activity(run_id: str) -> None:
    await SessionRegistry.instance().close(run_id)
