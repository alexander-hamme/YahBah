"""
Job alert email detection, URL extraction, page fetching, and scoring.

Detection is purely algorithmic — sender address is matched against
configurable wildcard patterns (unix-style, e.g. ``*@linkedin.com``).
No LLM fallback; unrecognized senders are silently skipped.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from html.parser import HTMLParser

import httpx
from loguru import logger

from yahbah.url_utils import normalize_job_url


# ---------------------------------------------------------------------------
# Sender detection (config-driven, wildcard patterns)
# ---------------------------------------------------------------------------

# Well-known source platforms by domain keyword.
_DOMAIN_TO_SOURCE: dict[str, str] = {
    "linkedin": "linkedin",
    "indeed": "indeed",
    "glassdoor": "glassdoor",
    "greenhouse": "greenhouse",
    "ziprecruiter": "ziprecruiter",
    "handshake": "handshake",
    "lever": "lever",
    "workday": "workday",
}


def _extract_email_addr(header: str) -> str:
    """Extract bare email from a From/To header like 'Name <email@x.com>'."""
    m = re.search(r"<([^>]+)>", header)
    return m.group(1).lower() if m else header.strip().lower()


def _source_from_addr(addr: str) -> str:
    """Derive a source platform name from an email address domain."""
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    for keyword, source in _DOMAIN_TO_SOURCE.items():
        if keyword in domain:
            return source
    return "other"


def sender_is_alert(sender: str, patterns: list[str]) -> str | None:
    """Check if sender matches any of the configured wildcard patterns.

    *patterns* come from ``gmail_alert_sender_patterns`` in settings.yaml
    and support standard unix-style wildcards (``fnmatch``).

    Returns the inferred source name (e.g. "linkedin") or None if no match.
    """
    addr = _extract_email_addr(sender)
    for pat in patterns:
        if fnmatch(addr, pat.lower()):
            return _source_from_addr(addr)
    return None


# ---------------------------------------------------------------------------
# URL extraction from HTML
# ---------------------------------------------------------------------------

@dataclass
class ExtractedJob:
    url: str       # normalized
    snippet: str   # link text near the URL
    source: str    # "linkedin", "indeed", etc.


# Domain patterns that indicate a job listing URL.
_JOB_URL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("linkedin", re.compile(r"linkedin\.com/(comm/)?jobs/view/")),
    ("indeed", re.compile(r"indeed\.com/(viewjob|rc/clk)")),
    ("greenhouse", re.compile(r"(boards|job-boards)\.greenhouse\.io/")),
    ("generic", re.compile(r"/(jobs|careers|positions|apply)/", re.IGNORECASE)),
]

# URLs to skip (tracking pixels, unsubscribe, settings, etc.)
_SKIP_PATTERNS = re.compile(
    r"(unsubscribe|preferences|settings|notifications|privacy|terms|help|"
    r"linkedin\.com/comm/jobs/search|linkedin\.com/e/|linkedin\.com/feed|"
    r"fonts\.googleapis|schema\.org|w3\.org)",
    re.IGNORECASE,
)


class _LinkExtractor(HTMLParser):
    """Extracts <a href> URLs and their link text."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, link_text)
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_href = href
                self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href:
            text = " ".join(self._current_text).strip()
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data.strip())


def _is_job_url(url: str) -> str | None:
    """Returns source name if URL looks like a job listing, else None."""
    if _SKIP_PATTERNS.search(url):
        return None
    for source, pattern in _JOB_URL_PATTERNS:
        if pattern.search(url):
            return source
    return None


async def extract_job_urls(html_body: str, source: str) -> list[ExtractedJob]:
    """Parse <a href> tags from alert email HTML, filter to job URLs, normalize."""
    parser = _LinkExtractor()
    try:
        parser.feed(html_body)
    except Exception:
        logger.warning("[alert-parser] Failed to parse HTML body")
        return []

    # Filter to job-relevant URLs
    raw_jobs: list[tuple[str, str, str]] = []  # (url, snippet, source)
    for href, link_text in parser.links:
        detected = _is_job_url(href)
        if detected:
            src = detected if detected != "generic" else source
            raw_jobs.append((href, link_text, src))

    if not raw_jobs:
        return []

    # Normalize URLs concurrently (semaphore limits parallelism)
    sem = asyncio.Semaphore(5)

    async def _normalize(url: str) -> str | None:
        async with sem:
            try:
                return await normalize_job_url(url)
            except Exception as exc:
                logger.debug(f"[alert-parser] Failed to normalize {url}: {exc}")
                return None

    normalized = await asyncio.gather(*[_normalize(url) for url, _, _ in raw_jobs])

    # Dedup by normalized URL
    seen: set[str] = set()
    results: list[ExtractedJob] = []
    for (_, snippet, src), norm_url in zip(raw_jobs, normalized):
        if norm_url and norm_url not in seen:
            seen.add(norm_url)
            results.append(ExtractedJob(url=norm_url, snippet=snippet or "", source=src))

    return results


# ---------------------------------------------------------------------------
# Page fetching + description extraction
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Strips HTML tags and returns visible text."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "head"})

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._text.append(text)

    def get_text(self) -> str:
        return " ".join(self._text)


def _extract_json_ld_description(html: str) -> str | None:
    """Try to extract job description from JSON-LD structured data."""
    pattern = re.compile(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting":
                    desc = item.get("description", "")
                    if desc:
                        extractor = _TextExtractor()
                        extractor.feed(desc)
                        return extractor.get_text()
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return None


def _extract_meta_description(html: str) -> str | None:
    """Extract from OG or meta description tags."""
    for pattern in [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m and len(m.group(1)) > 50:
            return m.group(1)
    return None


async def fetch_job_description(url: str) -> str | None:
    """Fetch a job page via HTTP and extract the description text.

    Tries JSON-LD structured data first, then meta tags, then raw page text.
    Returns None on failure.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.debug(f"[alert-parser] Failed to fetch {url}: {exc}")
        return None

    # Try extraction strategies in order of quality
    desc = _extract_json_ld_description(html)
    if desc and len(desc) > 100:
        return desc[:4000]

    meta = _extract_meta_description(html)
    if meta:
        page_text = _TextExtractor()
        page_text.feed(html)
        return f"{meta}\n\n{page_text.get_text()}"[:4000]

    extractor = _TextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    if len(text) > 100:
        return text[:4000]

    return None


# ---------------------------------------------------------------------------
# Match scoring (reuses existing FieldMapper infrastructure)
# ---------------------------------------------------------------------------

async def score_job(
    profile_text: str,
    job_description: str,
) -> tuple[int | None, str | None]:
    """Score a job using the existing match scoring system.

    Returns (score, rationale) or (None, None) on failure.
    """
    from yahbah.llm.field_mapper import FieldMapper
    try:
        mapper = FieldMapper()
        score, rationale = await mapper.compute_match_score(
            profile_text, job_description
        )
        return score, rationale
    except Exception as exc:
        logger.warning(f"[alert-parser] Failed to score job: {exc}")
        return None, None


def build_profile_summary() -> str:
    """Build the profile summary text for match scoring.

    Reads from personal.yaml (same source as the application workflow).
    """
    from yahbah.config import load_prompts_config
    config = load_prompts_config()
    profile = config.get("profile", {})

    summary = (
        f"Name: {profile.get('full_name', 'N/A')}\n"
        f"Location: {profile.get('location', 'N/A')}\n"
        f"Years of experience: {profile.get('years_of_experience', 'N/A')}\n"
        f"Skills: {', '.join(profile.get('skills', []))}\n"
        f"Bio: {profile.get('bio', 'N/A')}\n"
    )
    work_exp = profile.get("work_experience", [])
    relevant = [e for e in work_exp if e.get("use_in_custom_prompts", True)]
    if relevant:
        exp_lines = [
            f"  - {e.get('title', '')} at {e.get('company', '')} ({e.get('duration', '')})"
            for e in relevant
        ]
        summary += "Work experience:\n" + "\n".join(exp_lines)

    return summary
