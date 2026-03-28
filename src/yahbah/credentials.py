"""
Credential generation for ATS account creation.

- Email alias:  alexhamme96+greenhouse_acmecorp@gmail.com
- Password:     16-char, satisfies typical complexity requirements
"""
import re
import secrets
import string


def company_slug_from_url(job_url: str) -> str:
    """
    Derive a short company slug from the job URL.
    For Greenhouse: https://boards.greenhouse.io/acmecorp/jobs/123 → acmecorp
    Fallback: clean the hostname.
    """
    match = re.search(r"greenhouse\.io/([^/?#]+)", job_url)
    if match:
        return re.sub(r"[^a-z0-9]", "", match.group(1).lower())

    match = re.search(r"https?://(?:www\.)?([^/]+)", job_url)
    if match:
        return re.sub(r"[^a-z0-9]", "", match.group(1).lower())[:20]

    return "company"


def generate_email_alias(base_email: str, company_slug: str) -> str:
    """alexhamme96@gmail.com → alexhamme96+greenhouse_acmecorp@gmail.com"""
    local, domain = base_email.split("@", 1)
    return f"{local}+greenhouse_{company_slug}@{domain}"


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
