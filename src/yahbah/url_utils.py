"""
URL normalization utilities shared across the API and browser layers.

normalize_job_url() is the async entry point — it resolves redirects via HTTP,
strips tracking params, then applies ATS-specific structural normalization.

ats_normalize() and strip_tracking() are the sync building blocks used wherever
a URL has already been resolved (e.g. page.url from Playwright).
"""
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Query params that carry no identifying information about the job itself.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "source", "ref", "fbclid", "gclid", "msclkid",
})

# Greenhouse serves the same job application on multiple hostnames.
# Canonicalise all of them to boards.greenhouse.io.
_GREENHOUSE_HOSTS = frozenset({
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
})


def strip_tracking(url: str) -> str:
    """Remove known tracking query parameters from a URL."""
    parsed = urlparse(url)
    qs = {k: v for k, v in parse_qs(parsed.query).items() if k not in _TRACKING_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


def ats_normalize(url: str) -> str:
    """
    Apply ATS-specific structural normalization.
    Should be called after strip_tracking().
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if host in _GREENHOUSE_HOSTS:
        parsed = parsed._replace(netloc="boards.greenhouse.io")
        return urlunparse(parsed)

    return url


async def normalize_job_url(url: str) -> str:
    """
    Full normalization pipeline for a submitted job URL:
      1. Follow HTTP redirects to get the final URL
      2. Strip tracking params
      3. Apply ATS-specific normalization
    Falls back to applying steps 2-3 on the original URL if the HTTP request fails.
    """
    import httpx
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.head(url)
            resolved = str(resp.url)
    except Exception:
        resolved = url
    return ats_normalize(strip_tracking(resolved))
