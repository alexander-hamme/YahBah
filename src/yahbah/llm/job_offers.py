"""
What a job posting says it offers (contract C9 `job_offers`), as the verifier is shown it.

The metadata extractor (workflows/activities/llm.py, prompt `job_metadata`) reads the raw posting and returns
`JobOffersReply`: offered locations, remote, work arrangement, employment type, visa sponsorship and salary
bands, each with the posting's own words as `quote`. `validate_offers` checks that reply in code against
the posting text; whatever fails is dropped (list items) or set to unknown (single claims), and each drop is
logged with its field name only, never the text.

Honest limits:
- The extractor still reads the raw page, so injected text can steer which values it returns.
- A verbatim quote proves the words are on the page, not what they mean: "Remote work is not available"
  contains "remote", so `remote: yes` with that quote passes these checks, and a per-location salary band
  can be paired with the wrong location as long as both words are in its quote.
- The result stays untrusted page data. It is shown to the verifier only as claims about the job, to be
  judged against their quotes; a manipulated claim can still reach it and influence verdicts.

Quote matching: NFKC, case folding, typographic dashes and quotes made ASCII, whitespace collapsed, and
whole-word boundaries. `yahbah.agent.options.norm` is not used: it turns all punctuation into spaces, so
"$150,000" and "150 000", or "1.5" and "1 5", would match, and the currency signs, separators and decimal
points that give a quote's numbers their meaning would be lost.
"""
import re
import unicodedata
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel

__all__ = ["JobOffersReply", "MAX_LOCATIONS", "MAX_SALARY_BANDS", "amounts", "stated", "validate_offers"]

REMOTE = ("yes", "no", "unknown")  # can the job be done fully remotely (hybrid without a fully remote option: no)
WORK_ARRANGEMENT = ("onsite", "hybrid", "remote", "unknown")
EMPLOYMENT_TYPE = ("full_time", "part_time", "contract", "internship", "unknown")
VISA_SPONSORSHIP = ("offered", "not_offered", "unknown")
PERIODS = ("year", "month", "hour")
ENUMS = {"remote": REMOTE, "work_arrangement": WORK_ARRANGEMENT, "employment_type": EMPLOYMENT_TYPE,
         "visa_sponsorship": VISA_SPONSORSHIP}

MAX_LOCATIONS = 10
MAX_SALARY_BANDS = 5
MAX_PLACE_CHARS = 60
MAX_PLACE_WORDS = 6
MAX_QUOTE_CHARS = 200


# ── The extractor's reply (nested in the job_metadata reply) ─────────────────────

class _Location(BaseModel):
    place: str
    quote: str


class _Remote(BaseModel):
    value: Literal["yes", "no", "unknown"]
    quote: str | None


class _WorkArrangement(BaseModel):
    value: Literal["onsite", "hybrid", "remote", "unknown"]
    quote: str | None


class _EmploymentType(BaseModel):
    value: Literal["full_time", "part_time", "contract", "internship", "unknown"]
    quote: str | None


class _VisaSponsorship(BaseModel):
    value: Literal["offered", "not_offered", "unknown"]
    quote: str | None


class _SalaryBand(BaseModel):
    min: float | None
    max: float | None
    currency: str | None
    period: Literal["year", "month", "hour"] | None
    location: str | None
    quote: str


class JobOffersReply(BaseModel):
    """The C9 shape as the extractor writes it; `validate_offers` checks it against the posting."""
    locations: list[_Location]
    remote: _Remote
    work_arrangement: _WorkArrangement
    employment_type: _EmploymentType
    visa_sponsorship: _VisaSponsorship
    salary_bands: list[_SalaryBand]


# ── Text matching ─────────────────────────────────────────────────────────────

_TYPOGRAPHIC = str.maketrans({**{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"},
                              "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def _flat(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHIC).casefold().split())


def _contains(text: str, part: str) -> bool:
    """`part` appears in `text` as whole words, across whitespace, case and typographic differences."""
    needle = _flat(part)
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", _flat(text)) is not None


# ── Amounts ───────────────────────────────────────────────────────────────────

_SCALE = {"k": 1_000, "m": 1_000_000}
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?([km])?(?![a-z])")
# "150-180k" / "150 to 180K": the suffix written once applies to both ends.
_SHARED_SUFFIX = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s*(?:-|to)\s*\$?\s*\d+(?:\.\d+)?\s?([km])(?![a-z])")


def amounts(text: str) -> set[float]:
    """The amounts a text states: "$150,000" -> 150000, "150K-180K" -> 150000 and 180000, "1.2M" ->
    1200000, "$45.50/hour" -> 45.5; in "$150-180k" the shared suffix also reads 150 as 150000."""
    flat = _flat(text)
    out = set()
    for whole, fraction, suffix in _AMOUNT.findall(flat):
        value = float(whole.replace(",", "") + (f".{fraction}" if fraction else ""))
        out.add(round(value * _SCALE.get(suffix, 1), 2))
    for first, suffix in _SHARED_SUFFIX.findall(flat):
        out.add(round(float(first) * _SCALE[suffix], 2))
    return out


def _number(value: Any) -> float | int | None:
    """A JSON-friendly amount: int when whole."""
    return int(value) if float(value).is_integer() else value


# ── Validation ────────────────────────────────────────────────────────────────

def _dropped(field: str, reason: str) -> None:
    logger.info(f"[job_offers] dropped {field}: {reason}")


def _quote_problem(quote: Any, description: str) -> str | None:
    if not isinstance(quote, str) or not quote.strip():
        return "no quote"
    if len(quote.strip()) > MAX_QUOTE_CHARS:
        return "quote too long"
    if not _contains(description, quote):
        return "quote not in the description"
    return None


def _label_problem(label: Any) -> str | None:
    """A place or band location: one short line, no colon (it reads as a name, never as a key: value)."""
    if not isinstance(label, str) or not label.strip():
        return "empty"
    label = label.strip()
    if len(label) > MAX_PLACE_CHARS or len(label.split()) > MAX_PLACE_WORDS:
        return "too long"
    if "\n" in label or "\r" in label or ":" in label:
        return "not a single name"
    return None


def _claim(raw: dict, name: str, description: str) -> dict:
    unknown = {"value": "unknown", "quote": None}
    item = raw.get(name)
    if item is None:
        return unknown
    if not isinstance(item, dict):
        _dropped(name, "malformed")
        return unknown
    value, quote = item.get("value"), item.get("quote")
    if value == "unknown":
        return unknown
    if value not in ENUMS[name]:
        _dropped(name, "invalid value")
        return unknown
    if problem := _quote_problem(quote, description):
        _dropped(name, problem)
        return unknown
    return {"value": value, "quote": quote.strip()}


def _items(raw: dict, name: str) -> list:
    items = raw.get(name)
    if items is None:
        return []
    if not isinstance(items, list):
        _dropped(name, "malformed")
        return []
    return items


def _locations(raw: dict, description: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(_items(raw, "locations")):
        field = f"locations[{i}]"
        if not isinstance(item, dict):
            _dropped(field, "malformed")
            continue
        place, quote = item.get("place"), item.get("quote")
        problem = _label_problem(place) or _quote_problem(quote, description)
        if not problem and not _contains(quote, place):
            problem = "place not in its quote"
        if problem:
            _dropped(field, problem)
        elif _flat(place) not in seen:
            seen.add(_flat(place))
            out.append({"place": place.strip(), "quote": quote.strip()})
    if len(out) > MAX_LOCATIONS:
        _dropped(f"locations[{MAX_LOCATIONS}:]", "over the limit")
    return out[:MAX_LOCATIONS]


def _is_amount(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _band_problem(band: dict, description: str) -> str | None:
    lo, hi, quote, location = band.get("min"), band.get("max"), band.get("quote"), band.get("location")
    if lo is None and hi is None:
        return "no amount"
    if any(v is not None and not _is_amount(v) for v in (lo, hi)):
        return "invalid amount"
    if lo is not None and hi is not None and lo > hi:
        return "min above max"
    if band.get("period") not in (*PERIODS, None):
        return "invalid period"
    if problem := _quote_problem(quote, description):
        return problem
    stated = amounts(quote)
    if any(v is not None and round(float(v), 2) not in stated for v in (lo, hi)):
        return "amount not in its quote"
    if location is not None:
        if problem := _label_problem(location):
            return f"location {problem}"
        if not _contains(quote, location):
            return "location not in its quote"
    return None


def _currency(value: Any) -> str | None:
    return value.strip().upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value.strip()) else None


def _salary_bands(raw: dict, description: str) -> list[dict]:
    out: list[dict] = []
    for i, band in enumerate(_items(raw, "salary_bands")):
        field = f"salary_bands[{i}]"
        problem = "malformed" if not isinstance(band, dict) else _band_problem(band, description)
        if problem:
            _dropped(field, problem)
            continue
        lo, hi, location = band.get("min"), band.get("max"), band.get("location")
        out.append({"min": None if lo is None else _number(lo), "max": None if hi is None else _number(hi),
                    "currency": _currency(band.get("currency")), "period": band.get("period"),
                    "location": None if location is None else location.strip(), "quote": band["quote"].strip()})
    if len(out) > MAX_SALARY_BANDS:
        _dropped(f"salary_bands[{MAX_SALARY_BANDS}:]", "over the limit")
    return out[:MAX_SALARY_BANDS]


def validate_offers(raw: dict, description: str) -> dict | None:
    """The extractor's offers (JobOffersReply as a dict) checked against the posting text it read:
    every quote on the page, every stated value quoted, each place, band location and band amount in its
    own quote. The C9 dict, or None when nothing survives. Checks presence, not meaning (module docstring)."""
    if not isinstance(raw, dict):
        return None
    description = description or ""
    out = {"locations": _locations(raw, description),
           **{name: _claim(raw, name, description) for name in ENUMS},
           "salary_bands": _salary_bands(raw, description)}
    return out if stated(out) else None


def stated(offers: dict | None) -> dict:
    """The claims a C9 dict actually makes: unknown claims and empty lists left out."""
    return {k: v for k, v in (offers or {}).items() if v and not (isinstance(v, dict) and v.get("value") == "unknown")}
