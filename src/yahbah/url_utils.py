"""
URL intake policy, safe fetching and normalization, shared across the API and the Gmail layer.

validate_job_url() is the intake policy: http(s) only, on a host that is public. fetch_public()
applies it before every request and every redirect hop, and caps the response size.
normalize_job_url() is the async entry point: it resolves redirects through fetch_public(), then
canonicalize_url() applies RFC normalization, strips tracking params and runs ATS-specific cleanup.

canonicalize_url(), ats_normalize() and strip_tracking() are the sync building blocks used wherever
a URL has already been resolved (e.g. the page URL in the browser).

The browser agent does not apply this policy (it navigates on its own, and test fixtures are
served from 127.0.0.1); intake does, so a URL from an email or the API never reaches it unchecked.
"""
import asyncio
import ipaddress
import posixpath
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlparse, urlunparse

import httpx

# ---------------------------------------------------------------------------
# Intake policy
# ---------------------------------------------------------------------------

Resolver = Callable[[str], Awaitable[list[str]]]  # host -> IP address strings


class UnsafeURLError(ValueError):
    """The URL breaks the intake policy: not http(s), or its host is not public."""


async def _system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _non_public_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why *ip* is not a public unicast address, or None if it is."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return _non_public_reason(embedded)
    for kind in ("loopback", "private", "link_local", "multicast", "reserved", "unspecified"):
        if getattr(ip, f"is_{kind}"):
            return kind.replace("_", "-")
    if not ip.is_global:  # e.g. 100.64.0.0/10 (CGNAT)
        return "non-global"
    return None


async def validate_job_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Returns *url* (stripped) if the intake policy allows it; raises UnsafeURLError otherwise.

    Allowed: an http(s) URL without credentials whose host is a public IP literal, or a name that
    resolves only to public addresses. Rejected: other schemes, localhost names, non-canonical
    numeric hosts (2130706433, 127.1), and loopback, private (RFC 1918, ULA), link-local, CGNAT,
    multicast, reserved and unspecified addresses, also when embedded in IPv6. A host that doesn't
    resolve is rejected too. *resolver* defaults to the system resolver.
    """
    url = url.strip()
    try:
        parsed = urlparse(url)
        parsed.port  # raises on a malformed port
    except ValueError as exc:
        raise UnsafeURLError(f"malformed URL ({exc})") from None
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError(f"scheme {parsed.scheme or '(none)'!r} is not allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("URLs with credentials are not allowed")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURLError(f"host {host!r} is local")

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:  # resolvers read these as IPv4 addresses
            raise UnsafeURLError(f"non-canonical numeric host {host!r}")
        try:
            resolved = await (resolver or _system_resolver)(host)
            addresses = [ipaddress.ip_address(a.split("%", 1)[0]) for a in resolved]
        except Exception:
            raise UnsafeURLError(f"host {host!r} does not resolve") from None
        if not addresses:
            raise UnsafeURLError(f"host {host!r} does not resolve")

    for ip in addresses:
        if reason := _non_public_reason(ip):
            raise UnsafeURLError(f"host {host!r} is a {reason} address")
    return url


# ---------------------------------------------------------------------------
# Fetching under the policy
# ---------------------------------------------------------------------------

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 10
_USER_AGENT = "Mozilla/5.0 (compatible; bot)"


@dataclass(frozen=True)
class FetchResult:
    url: str           # final URL, after redirects
    status_code: int
    content_type: str  # media type, lowercased, without parameters ("" if absent)
    text: str          # decoded body, at most max_bytes of it ("" for HEAD)
    truncated: bool    # the body was longer than max_bytes


async def fetch_public(
    method: str,
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = _MAX_RESPONSE_BYTES,
    timeout: float = 15.0,
) -> FetchResult:
    """One HTTP request under the intake policy.

    The URL and every redirect hop are validated, as httpx parsed them, before they are requested;
    redirects are followed by hand, at most _MAX_REDIRECTS. At most *max_bytes* of the body are
    read. Raises UnsafeURLError, or an httpx error on transport failure or too many redirects.
    """
    async with httpx.AsyncClient(
        transport=transport, timeout=timeout, headers={"User-Agent": _USER_AGENT},
    ) as client:
        try:
            request = client.build_request(method, url)
        except httpx.InvalidURL as exc:
            raise UnsafeURLError(f"malformed URL ({exc})") from None
        for _ in range(_MAX_REDIRECTS + 1):
            await validate_job_url(str(request.url), resolver=resolver)
            response = await client.send(request, stream=True)
            try:
                if response.next_request is not None:  # a redirect; not followed automatically
                    request = response.next_request
                    continue
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        break
                return FetchResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type", "").split(";")[0].strip().lower(),
                    text=bytes(body[:max_bytes]).decode(response.encoding or "utf-8", errors="replace"),
                    truncated=len(body) > max_bytes,
                )
            finally:
                await response.aclose()
    raise httpx.TooManyRedirects(f"more than {_MAX_REDIRECTS} redirects", request=request)


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
    # Greenhouse (not gh_jid: on company-hosted career pages it is the job id)
    "gh_src",
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

# Tracking params only one site uses; on other hosts the same name may be job identity.
_HOST_TRACKING_PARAMS: dict[str, frozenset[str]] = {
    "jobright.ai": frozenset({"imp_id"}),  # per-email impression id on every alert link
}


def _host_tracking_params(host: str) -> frozenset[str]:
    for domain, params in _HOST_TRACKING_PARAMS.items():
        if host == domain or host.endswith("." + domain):
            return params
    return frozenset()


def strip_tracking(url: str) -> str:
    """Remove known tracking query parameters from a URL.

    Every other parameter is kept as written and in order, blank ones included (``?iis=``):
    a parameter we don't know may be part of the job's identity.
    """
    parsed = urlparse(url)
    drop = _TRACKING_PARAMS | _host_tracking_params((parsed.hostname or "").lower())
    kept = [
        p for p in parsed.query.split("&")
        if p and unquote_plus(p.split("=", 1)[0]) not in drop
    ]
    return urlunparse(parsed._replace(query="&".join(kept)))


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
    if ":" in host:  # IPv6 literal: hostname drops the brackets
        host = f"[{host}]"

    # Remove default ports
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"

    # Normalize path: resolve .. and . segments
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


def canonicalize_url(url: str) -> str:
    """Normalize an already-resolved job URL: RFC rules, tracking params, ATS cleanup. Idempotent."""
    return ats_normalize(strip_tracking(_normalize_url(url)))


# ---------------------------------------------------------------------------
# Full normalization pipeline (async — follows redirects)
# ---------------------------------------------------------------------------

async def normalize_job_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Full normalization pipeline for a job URL:

    1. Follow HTTP redirects (HEAD, under the intake policy) to get the final URL
    2. canonicalize_url(): RFC normalization, tracking params, ATS-specific cleanup

    Raises UnsafeURLError if the URL or any redirect hop breaks the policy. If the request fails
    for any other reason, the original URL is canonicalized instead.
    """
    try:
        resolved = (await fetch_public("HEAD", url, resolver=resolver, transport=transport)).url
    except UnsafeURLError:
        raise
    except Exception:
        resolved = url

    return canonicalize_url(resolved)
