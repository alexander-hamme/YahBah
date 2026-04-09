"""
Credential generation for ATS account creation.

- Email alias:  user+job-a7f2@gmail.com  (4-char hash, extended on collision)
- Password:     16-char, satisfies typical complexity requirements
"""
import hashlib
import re
import secrets
import string

from sqlalchemy import select

# Minimum hash length; extended by one char on each collision (like Git short SHAs).
_MIN_HASH_LEN = 4
_MAX_HASH_LEN = 8


def _full_hash(ats: str, company: str, job_id: str) -> str:
    """Full hex digest from ats + company + job_id."""
    raw = f"{ats}:{company}:{job_id}".lower()
    return hashlib.sha256(raw.encode()).hexdigest()


def _extract_job_parts(job_url: str) -> tuple[str, str, str]:
    """
    Extract (ats, company, job_id) from a job URL.
    Returns best-effort values for hashing.
    """
    # Greenhouse: boards.greenhouse.io/<company>/jobs/<id>
    match = re.search(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", job_url)
    if match:
        return "greenhouse", match.group(1).lower(), match.group(2)

    # Greenhouse without job ID
    match = re.search(r"greenhouse\.io/([^/?#]+)", job_url)
    if match:
        return "greenhouse", match.group(1).lower(), ""

    # Workday: <company>.wd<N>.myworkdayjobs.com/.../job/<id>
    match = re.search(r"([^.]+)\.wd\d+\.myworkdayjobs\.com", job_url)
    if match:
        company = match.group(1).lower()
        id_match = re.search(r"/(\d+)", job_url)
        job_id = id_match.group(1) if id_match else ""
        return "workday", company, job_id

    # Generic fallback: hostname + full path for uniqueness
    match = re.search(r"https?://(?:www\.)?([^/]+)(.*)", job_url)
    if match:
        host = re.sub(r"[^a-z0-9]", "", match.group(1).lower())[:20]
        path = match.group(2)
        return "other", host, path

    return "other", "unknown", job_url


async def generate_email_alias(base_email: str, job_url: str) -> str:
    """
    user@gmail.com → user+job-a7f2@gmail.com

    Starts with a 4-char hash; extends by one character on each collision
    (like Git short SHAs) up to 8 chars max.
    """
    from yahbah.db.models import ApplicationRun
    from yahbah.db.session import AsyncSessionLocal

    ats, company, job_id = _extract_job_parts(job_url)
    digest = _full_hash(ats, company, job_id)
    local, domain = base_email.split("@", 1)

    async with AsyncSessionLocal() as session:
        for length in range(_MIN_HASH_LEN, _MAX_HASH_LEN + 1):
            tag = digest[:length]
            candidate = f"{local}+job-{tag}@{domain}"
            result = await session.execute(
                select(ApplicationRun.id).where(
                    ApplicationRun.account_email == candidate
                )
            )
            if result.scalar_one_or_none() is None:
                return candidate

    # Extremely unlikely: all lengths collided. Use full 8-char hash.
    return f"{local}+job-{digest[:_MAX_HASH_LEN]}@{domain}"


def generate_password() -> str:
    """
    16-character password guaranteed to satisfy common ATS password policies:
    at least one uppercase, lowercase, digit, and special character.
    """
    special = "!@#$%^&*"
    pool = string.ascii_letters + string.digits + special

    # Satisfy complexity requirements
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(special),
    ]
    rest = [secrets.choice(pool) for _ in range(12)]

    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
