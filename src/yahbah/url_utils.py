"""
URL normalization utilities shared across the API and browser layers.

normalize_job_url() is the async entry point — it resolves redirects via HTTP,
then applies RFC-compliant normalization, strips tracking params, and runs
ATS-specific structural cleanup.

ats_normalize() and strip_tracking() are the sync building blocks used wherever
a URL has already been resolved (e.g. page.url from Playwright).
"""
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# Tracking parameter removal
# ---------------------------------------------------------------------------

# Comprehensive list of tracking/analytics query params. Sources: Firefox ETP,
# Brave de-AMP, ClearURLs, uBlock Origin.
_TRACKING_PARAMS = frozenset({
    # UTM (Google Analytics)
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "utm_creative_format", "utm_marketing_tactic",
    # Meta / Facebook
    "fbclid", "fb_action_ids", "fb_action_types", "fb_source", "fb_ref",
    # Google Ads / DoubleClick
    "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    # Microsoft / Bing
    "msclkid",
    # LinkedIn
    "li_fat_id", "li_at", "trk", "trkInfo", "refId", "trackingId",
    "lipi", "licu",
    # Greenhouse
    "gh_src", "gh_jid",
    # General referral / source
    "source", "ref", "ref_", "referrer",
    # HubSpot
    "hsa_cam", "hsa_grp", "hsa_mt", "hsa_src", "hsa_ad", "hsa_acc",
    "hsa_net", "hsa_ver", "hsa_la", "hsa_ol", "hsa_kw",
    # Mailchimp
    "mc_cid", "mc_eid",
    # Miscellaneous
    "icid", "igshid", "s_kwcid", "twclid", "ttclid", "yclid",
    "zanpid", "spm", "share_id", "_hsenc", "_hsmi",
})


def strip_tracking(url: str) -> str:
    """Remove known tracking query parameters from a URL."""
    parsed = urlparse(url)
    qs = {k: v for k, v in parse_qs(parsed.query).items() if k not in _TRACKING_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


# ---------------------------------------------------------------------------
# RFC-compliant URL normalization
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """Apply standard URL normalization (RFC 3986 / RFC 7230).

    - Lowercase scheme and host
    - Remove default ports (80 for http, 443 for https)
    - Remove trailing slash on path (unless root)
    - Remove empty query string
    - Remove fragment
    - Collapse path segments (e.g. /a/../b → /b)
    """
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()

    # Remove default ports
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"

    # Normalize path: resolve .. and . segments
    import posixpath
    path = posixpath.normpath(parsed.path) if parsed.path else "/"
    # normpath strips trailing slash and turns empty to '.'; fix those
    if path == ".":
        path = "/"
    # Preserve trailing slash only for root
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Remove empty query and fragment
    query = parsed.query  # tracking params stripped separately
    fragment = ""  # always drop fragments

    return urlunparse((scheme, netloc, path, parsed.params, query, fragment))


# ---------------------------------------------------------------------------
# ATS-specific structural normalization
# ---------------------------------------------------------------------------

# Greenhouse serves the same job application on multiple hostnames.
_GREENHOUSE_HOSTS = frozenset({
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
})


def ats_normalize(url: str) -> str:
    """Apply ATS-specific structural normalization.

    Should be called after strip_tracking().
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Greenhouse: canonicalize host
    if host in _GREENHOUSE_HOSTS:
        parsed = parsed._replace(netloc="boards.greenhouse.io")
        return urlunparse(parsed)

    # LinkedIn: strip /comm/ wrapper (used in email click-tracking)
    if host == "www.linkedin.com" or host == "linkedin.com":
        path = parsed.path
        if path.startswith("/comm/"):
            path = path[len("/comm"):]  # /comm/jobs/view/123 → /jobs/view/123
            parsed = parsed._replace(path=path)
        # Normalize host to www.linkedin.com
        parsed = parsed._replace(netloc="www.linkedin.com")
        return urlunparse(parsed)

    return url


# ---------------------------------------------------------------------------
# Full normalization pipeline (async — follows redirects)
# ---------------------------------------------------------------------------

async def normalize_job_url(url: str) -> str:
    """Full normalization pipeline for a job URL:

    1. Follow HTTP redirects to get the final URL
    2. RFC-compliant normalization (lowercase, remove fragments, etc.)
    3. Strip tracking query parameters
    4. Apply ATS-specific normalization
    Falls back to steps 2-4 on the original URL if the HTTP request fails.
    """
    import httpx
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"},
        ) as client:
            resp = await client.head(url)
            resolved = str(resp.url)
    except Exception:
        resolved = url

    return ats_normalize(strip_tracking(_normalize_url(resolved)))
