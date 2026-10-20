"""
answer_cache: grounded factual answers to custom questions, reused only while what backs them holds.

Written only for a non-generated answer with a basis (stored-answer keys / profile fields) to a
question that is neither conditional ("If yes, please explain", "Other", very short labels) nor
about the specific company or job. Generated prose (generated=True) is never cached: each run's
review shows it fresh.

Each row records its provenance: the namespace ("live", or "debug" for scripts/run_pipeline.py),
the basis keys, `basis_hash` (their current values plus the set of stored-answer keys), the prose
model, a hash of the prompt, and the run that produced it. A row is reused only while all of them
still match: editing a cited value, adding or removing a stored answer, or changing the prompt or
model turns it into a miss, and the next answer overwrites it. The mapper resolves stored answers
before it looks here, so they always win.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from yahbah.db.models import AnswerCache
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.logs import current_run_id

LIVE, DEBUG = "live", "debug"

_CONDITIONAL_RE = re.compile(
    r"^\W*(if|when)\b|\bif (yes|no|so|applicable|other|selected|you answered)\b|^\W*other\b"
    r"|please (specify|explain|elaborate|describe|clarify|provide (more |further |additional )?(details|information))")
_JOB_CONTEXT_RE = re.compile(
    r"\b(us|our|we|here|join)\b|\bthis (role|position|job|team|company|opportunity|posting|organi[sz]ation|program)\b"
    r"|\bwhy (do you want|are you interested|would you like)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def question_key(label: str, options: list[str]) -> str:
    raw = _norm(label) + "|" + "|".join(sorted(_norm(o) for o in options))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def basis_hash(basis: list[str], facts: dict[str, str], stored_keys: list[str]) -> str | None:
    """Hash of the cited values and of which stored answers exist; None if a cited key is gone."""
    if any(k not in facts for k in basis):
        return None
    payload = json.dumps({"basis": {k: facts[k] for k in sorted(basis)}, "stored": sorted(stored_keys)})
    return text_hash(payload)


def is_conditional(label: str) -> bool:
    """A follow-up whose meaning depends on an earlier answer ("If yes, please explain", "Other")."""
    return bool(_CONDITIONAL_RE.search(label.casefold()))


def cacheable(label: str, job_description: str = "") -> bool:
    """False for conditional, very short, or company/job-specific labels (a capitalized name from
    the job description, "this role", "us", ...): their answer depends on context."""
    if len(_norm(label).split()) < 3 or is_conditional(label) or _JOB_CONTEXT_RE.search(label):
        return False
    names = [w for w in re.findall(r"\b[A-Z][\w&.-]{2,}", label)[1:]]  # capitalized words after the first
    return not any(re.search(rf"\b{re.escape(n)}\b", job_description) for n in names)


async def lookup(namespace: str, label: str, options: list[str], *, facts: dict[str, str],
                 stored_keys: list[str], model: str, prompt_hash: str) -> tuple[str, list[str]] | None:
    """(answer, basis) of a row whose provenance still holds, else None."""
    async with AsyncSessionLocal() as session:
        row = await session.get(AnswerCache, (namespace, question_key(label, options)))
        if row is None:
            return None
        current = basis_hash(row.basis, facts, stored_keys)
        if (row.model, row.prompt_hash, row.basis_hash) != (model, prompt_hash, current):
            logger.info(f"[map] cached answer to {label!r} is stale (basis, prompt or model changed)")
            return None
        row.hits += 1
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return row.answer, list(row.basis)


def _run_uuid() -> uuid.UUID | None:
    try:
        return uuid.UUID(current_run_id() or "")
    except ValueError:
        return None  # outside a run, or a debug run id ("debug-<timestamp>")


async def store(namespace: str, label: str, options: list[str], answer: str, basis: list[str], *,
                facts: dict[str, str], stored_keys: list[str], model: str, prompt_hash: str) -> None:
    digest = basis_hash(basis, facts, stored_keys)
    if not basis or digest is None:
        return
    values = dict(namespace=namespace, question_key=question_key(label, options), question=label, options=options,
                  answer=answer, basis=basis, basis_hash=digest, model=model, prompt_hash=prompt_hash,
                  run_id=_run_uuid(), hits=0, updated_at=datetime.now(timezone.utc))
    statement = insert(AnswerCache).values(**values, created_at=values["updated_at"])
    statement = statement.on_conflict_do_update(
        index_elements=["namespace", "question_key"],
        set_={k: statement.excluded[k] for k in values if k not in ("namespace", "question_key")})
    async with AsyncSessionLocal() as session:
        await session.execute(statement)
        await session.commit()
