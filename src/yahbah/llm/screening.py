"""
Typed screening facts: the material questions an application may answer only from a stored fact.

`classify(label)` decides in code (keyword rules, no model) which fact kinds a question asks about:

  work_authorization, sponsorship, citizenship   per jurisdiction
  skill_years                                    per skill ("years of experience with Python")
  total_years, start_date, salary, clearance, age, relocation, background_check, criminal_record,
  prior_employment, non_compete, eeo_gender / eeo_race / eeo_hispanic / eeo_veteran /
  eeo_disability / eeo_orientation / eeo_transgender / eeo_pronouns

`typed_facts(answers)` types the stored answers by key name (`fact_kinds(key)`):
  - `work_authorization`, `sponsorship_required`, `citizenship` without a suffix describe the
    country of residence (stored `country_of_residence`, default the United States); other
    jurisdictions are suffixed keys: `work_authorization_germany`, `sponsorship_required_eu`, ...
    `work_status` ("US Citizen") counts for authorization and citizenship in the jurisdiction its
    value names, else the country of residence.
  - per-skill years are `years_<skill>` (`years_python`); total years is the profile's
    `years_of_experience`.
A jurisdiction matches only itself (an EU fact does not answer a Germany question).

`resolve(...)` answers a classified question from those facts in code where it can (the stored
value as is, a flipped yes/no for "...without sponsorship?", a years threshold or range), and
otherwise names the facts a constrained model call may use. No fact for the question's kinds and
scope -> no answer: the caller raises NeedsAnswer. The job description is never a fact; it only
tells which country an unscoped question ("Will you require sponsorship?") is about.
"""
import re
from dataclasses import dataclass, field

# ── Jurisdictions ─────────────────────────────────────────────────────────────

HOME_DEFAULT = "US"
AMBIGUOUS = "?"  # the job's country can't be told (several countries named, none clearly the job's)

# Names that identify a jurisdiction in a question, a location string or a stored key's suffix.
_NAMES: dict[str, tuple[str, ...]] = {
    "US": ("united states of america", "united states", "usa", "u.s.a.", "u.s.", "america"),
    "CA": ("canada",), "UK": ("united kingdom", "great britain", "britain", "england", "scotland", "wales",
                               "northern ireland", "uk", "u.k."),
    "EU": ("european union", "european economic area", "eea", "eu", "schengen"),
    "DE": ("germany", "deutschland"), "FR": ("france",), "NL": ("netherlands", "holland"), "IE": ("ireland",),
    "ES": ("spain",), "IT": ("italy",), "CH": ("switzerland",), "AT": ("austria",), "PL": ("poland",),
    "SE": ("sweden",), "DK": ("denmark",), "NO": ("norway",), "FI": ("finland",), "BE": ("belgium",),
    "PT": ("portugal",), "CZ": ("czech republic", "czechia"), "IN": ("india",), "AU": ("australia",),
    "NZ": ("new zealand",), "SG": ("singapore",), "JP": ("japan",), "IL": ("israel",), "MX": ("mexico",),
    "BR": ("brazil",), "AE": ("united arab emirates", "uae"), "CN": ("china",), "KR": ("south korea", "korea"),
}
# Only in questions and key suffixes: demonyms ("a German work permit") and bare codes.
_QUESTION_ONLY: dict[str, tuple[str, ...]] = {
    "US": ("american",), "CA": ("canadian",), "UK": ("british",), "EU": ("european",), "DE": ("german",),
    "FR": ("french",), "NL": ("dutch",), "IE": ("irish",), "IN": ("indian",), "AU": ("australian",),
}
# Only in locations and job descriptions: cities a posting names without the country.
_CITIES: dict[str, tuple[str, ...]] = {
    "DE": ("munich", "münchen", "berlin", "hamburg", "frankfurt", "cologne", "köln", "stuttgart", "düsseldorf"),
    "UK": ("london", "manchester", "edinburgh", "bristol", "glasgow"), "CA": ("toronto", "vancouver",
                                                                             "montreal", "ottawa", "calgary"),
    "FR": ("paris", "lyon"), "NL": ("amsterdam", "rotterdam", "eindhoven"), "IE": ("dublin",),
    "CH": ("zurich", "zürich", "geneva", "lausanne"), "IN": ("bangalore", "bengaluru", "hyderabad", "pune"),
    "AU": ("sydney", "melbourne"), "ES": ("madrid", "barcelona"), "PL": ("warsaw", "krakow", "kraków"),
    "SE": ("stockholm",), "DK": ("copenhagen",), "PT": ("lisbon",), "IL": ("tel aviv",), "JP": ("tokyo",),
    "AT": ("vienna",), "CZ": ("prague",),
}
_US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
)
_US_STATE_CODES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()


def _phrase_re(phrases) -> re.Pattern:
    alternatives = sorted({re.escape(p) for p in phrases}, key=len, reverse=True)
    return re.compile(r"(?<![\w.])(?:" + "|".join(alternatives) + r")(?![\w])", re.I)


_QUESTION_RES = {code: _phrase_re(_NAMES.get(code, ()) + _QUESTION_ONLY.get(code, ()))
                 for code in set(_NAMES) | set(_QUESTION_ONLY)}
_TEXT_RES = {code: _phrase_re(_NAMES.get(code, ()) + _CITIES.get(code, ()))
             for code in set(_NAMES) | set(_CITIES)}
_TEXT_RES["US"] = _phrase_re(_NAMES["US"] + _US_STATES)
_US_CODE_RE = re.compile(r"\bUS\b|\bU\.S\.")  # case-sensitive: "us" is a pronoun
_US_STATE_CODE_RE = re.compile(r"[A-Za-z.], (?:" + "|".join(_US_STATE_CODES) + r")\b")  # "Boston, MA"


def _found(text: str, patterns: dict[str, re.Pattern]) -> list[str]:
    """Jurisdiction codes named in text, in order of first mention."""
    hits = [(m.start(), code) for code, rx in patterns.items() for m in [rx.search(text)] if m]
    if "US" not in {c for _, c in hits}:
        m = _US_CODE_RE.search(text) or (_US_STATE_CODE_RE.search(text) if patterns is _TEXT_RES else None)
        if m:
            hits.append((m.start(), "US"))
    return [code for _, code in sorted(hits)]


def question_jurisdictions(label: str) -> list[str]:
    return _found(label, _QUESTION_RES)


def text_jurisdictions(text: str) -> list[str]:
    """Jurisdictions a location string or job description names (countries, cities, US states)."""
    return _found(text, _TEXT_RES)


def jurisdiction_code(name: str) -> str | None:
    """The jurisdiction a value or key suffix names ("United States", "germany", "eu"), else None."""
    text = name.replace("_", " ").strip()
    if text.upper() in _NAMES:  # an ISO-style code: work_authorization_de
        return text.upper()
    found = _found(text, _QUESTION_RES)
    return found[0] if len(set(found)) == 1 else None


def _location_country(location: str) -> str | None:
    """A trailing country code of a structured location ("Munich, BY, DE", "Austin, TX, USA")."""
    parts = [p.strip() for p in location.split(",")]
    last = parts[-1] if parts else ""
    if last in ("US", "USA"):
        return "US"
    if last == "GB":
        return "UK"
    if last in _NAMES and (last not in _US_STATE_CODES or len(parts) >= 3):
        return last
    return None


def job_jurisdiction(job_location: str | None, job_description: str) -> str | None:
    """The job's country: from its location if that names exactly one, else from the job description
    (one country named -> it; none -> None, read as the country of residence; several -> AMBIGUOUS)."""
    locations = [p for p in re.split(r"\s*\|\s*|;", job_location or "") if p.strip()]
    coded = {_location_country(p) for p in locations}
    if locations and None not in coded:
        return coded.pop() if len(coded) == 1 else AMBIGUOUS
    for text in (job_location or "", job_description or ""):
        found = set(text_jurisdictions(text))
        if len(found) == 1:
            return found.pop()
        if len(found) > 1:
            return AMBIGUOUS
    return None


# ── Fact kinds ────────────────────────────────────────────────────────────────

JURISDICTIONAL = ("work_authorization", "sponsorship", "citizenship")

# Question patterns per kind (on the lower-cased label).
_KIND_RES: list[tuple[str, re.Pattern]] = [(k, re.compile(p)) for k, p in [
    ("sponsorship", r"sponsor|\bvisa\b|\bh-?1b\b|immigration (case|petition|support|status)"),
    ("work_authorization", r"authori[sz]ed to (work|be employed)|authori[sz]ation to work|work(ing)? authori[sz]ation"
                           r"|employment authori[sz]ation|right to work|(legally|lawfully) (eligible|permitted|able|"
                           r"allowed|entitled|authori[sz]ed)|eligib(le|ility) (to|for) (work|employment|be employed)"
                           r"|work permit|permission to work|(permitted|allowed|entitled) to work|employment eligibility"),
    ("citizenship", r"\bcitizen(ship)?\b|nationality|permanent resident|green card holder"),
    ("clearance", r"clearance|\bts/sci\b|top secret|polygraph"),
    ("start_date", r"start(ing)? date|earliest (possible )?start|when (can|could|would|will) you (start|begin|join)"
                   r"|available (from|to (start|begin|join))|^\W*availab(le|ility)\W*$|availability (date|to start)"
                   r"|date (of )?availab|notice period|how (soon|quickly) (can|could|would) you (start|join|begin)"
                   r"|(can|could) you (start|begin|join) (on|by|within|immediately)|date you can start"),
    ("salary", r"salary|compensation|\bpay\b|\bwage|hourly rate|rate expectation|desired rate|expected rate"
               r"|\bctc\b|\bote\b|remuneration"),
    ("eeo_transgender", r"transgender"),
    ("eeo_orientation", r"sexual orientation"),
    ("eeo_gender", r"\bgender\b|^\W*sex\W*$|\bsex\b.{0,20}(identify|describe|select)"),
    ("eeo_hispanic", r"hispanic|latin[oax]\b"),
    ("eeo_race", r"\brace\b(?! condition)|ethnic"),
    ("eeo_veteran", r"veteran|military service|armed forces"),
    ("eeo_disability", r"disabilit"),
    ("eeo_pronouns", r"pronoun"),
    ("age", r"\b(16|18|21) (years|yrs)\b|legal (working )?age|\bof age\b|\bage\b|how old|date of birth|birth ?date|\bdob\b"),
    ("relocation", r"(willing|open|able|plan\w*|consider\w*|prepared|need|requir\w*|interested)\b.{0,30}relocat"
                   r"|relocat\w*\W*$|relocation (assistance|support|package)"),
    ("background_check", r"background (check|screen|investigation)|drug (test|screen)|pre-employment screen"),
    ("criminal_record", r"convicted|conviction|criminal|felony|misdemeanou?r"),
    ("prior_employment", r"(previously|ever|before|formerly) (been )?(employed|worked)|worked (for|at|with) .{0,40}"
                         r"(before|previously)|(current or )?former employee|ex-employee"),
    ("non_compete", r"non-?compete|non-?solicit|restrictive covenant"),
]]
_YEARS_RE = re.compile(r"\byears?\b|\byrs?\b|how long")
_YEARS_CONTEXT_RE = re.compile(r"experience|worked|working|using|used|coding|programming|developing|hands-on")
# STAR and open prompts are narratives even when they mention a topic ("a race condition you debugged").
_NARRATIVE_RE = re.compile(r"^\W*(tell (us|me)|describe|walk (us|me) through|give (us |me )?an? example|share an?"
                           r"|explain how|what (did|would) you (learn|do))\b|tell us about a time|describe a (time|situation)")


def classify(label: str) -> set[str]:
    """The fact kinds a question asks about; empty for anything that isn't a screening question."""
    text = " ".join(label.casefold().split())
    if _NARRATIVE_RE.search(text) and not re.search(r"sponsor|authori[sz]|visa|salary|compensation|notice period", text):
        return set()
    kinds = {kind for kind, rx in _KIND_RES if rx.search(text)}
    if _YEARS_RE.search(text) and _YEARS_CONTEXT_RE.search(text) and "age" not in kinds:
        kinds.add("skill_years" if skill_phrases(label) else "total_years")
    if "age" in kinds and ("skill_years" in kinds or "total_years" in kinds):
        kinds.discard("age")
    return kinds


# Words that don't name a skill in "years of <...> experience" (what remains is the skill).
_GENERIC = {"professional", "relevant", "industry", "commercial", "hands-on", "hands", "on", "work", "working",
            "full-time", "full", "time", "total", "overall", "paid", "prior", "previous", "related", "the", "a",
            "an", "any", "your", "of", "experience", "years", "year", "do", "you", "have", "direct", "practical",
            "real-world", "post-graduate", "postgraduate", "combined", "applicable", "technical", "setting",
            "environment", "role", "roles", "position", "capacity", "field", "company", "team", "this", "that",
            "similar", "such", "these", "it", "them"}
_SUBJECT_RES = [re.compile(p, re.I) for p in (
    r"\b(?:experience|worked|working|been using|used|using|coded|coding|programmed|programming|developed|developing)"
    r"\b.{0,30}?\b(?:with|in|using|on)\s+(.+?)(?:\?|$|\(| - |:|\.\s)",
    r"years? (?:of )?((?:[\w#+./-]+ ){1,5}?)experience",
    r"years? (?:with|in|using)\s+(.+?)(?:\?|$|\()",
)]


def skill_phrases(label: str) -> list[str]:
    """The skills a years question is about, normalized ("TypeScript and React" -> typescript, react)."""
    for rx in _SUBJECT_RES:
        m = rx.search(label)
        if not m:
            continue
        phrases = []
        for part in re.split(r",|/|&|\band\b|\bor\b|\bplus\b", m.group(1), flags=re.I):
            words = [w for w in re.findall(r"[\w#+.-]+", part.casefold()) if w not in _GENERIC and not w.isdigit()]
            if words:
                phrases.append(" ".join(words).strip(". "))
        if phrases:
            return phrases
    return []


@dataclass(frozen=True)
class Fact:
    key: str          # the stored-answer key or profile field cited in basis
    kind: str
    value: str
    scope: str | None = None  # jurisdiction code, skill name (normalized), or None


def _skill_norm(s: str) -> str:
    return " ".join(re.findall(r"[\w#+]+", s.casefold().replace("_", " ")))


def fact_kinds(key: str, value: str, home: str) -> list[tuple[str, str | None]]:
    """(kind, scope) pairs a stored key is a typed fact for; [] for an untyped key."""
    k = key.casefold()
    def scoped(suffix: str | None) -> str | None:
        return home if not suffix else jurisdiction_code(suffix) or f"?{suffix}"
    for kind, rx in (("work_authorization", r"(?:work_authori[sz]ation|authori[sz]ed_to_work|right_to_work|work_permit)"),
                     ("sponsorship", r"(?:sponsorship_required|requires?_sponsorship|needs?_sponsorship|"
                                     r"visa_sponsorship(?:_required)?|sponsorship)"),
                     ("citizenship", r"(?:citizenship|citizen)")):
        m = re.fullmatch(rx + r"(?:_(.+))?", k)
        if m:
            return [(kind, scoped(m.group(1)))]
    if k == "work_status":
        where = jurisdiction_code(value) if jurisdiction_code(value) else home
        return [("work_authorization", where), ("citizenship", where)]
    if k in ("years_of_experience", "years_experience", "total_years_of_experience", "total_years_experience"):
        return [("total_years", None)]
    m = re.fullmatch(r"years_(?:of_|with_|in_|using_)?(.+?)(?:_experience)?", k) or re.fullmatch(r"(.+)_years", k)
    if m:
        return [("skill_years", _skill_norm(m.group(1)))]
    rules = [
        ("start_date", r"start_date|^availab|notice|when_can_you_start"),
        ("salary", r"salary|compensation|pay_expectation|desired_pay|hourly_rate"),
        ("clearance", r"clearance"),
        ("eeo_transgender", r"transgender"), ("eeo_orientation", r"sexual_orientation"),
        ("eeo_gender", r"gender|^sex$"), ("eeo_hispanic", r"hispanic|latin"), ("eeo_race", r"race|ethnic"),
        ("eeo_veteran", r"veteran"), ("eeo_disability", r"disabilit"), ("eeo_pronouns", r"pronoun"),
        ("age", r"(^|_)(18|21|age)(_|$)|legal_age|over_18|at_least_18"),
        ("relocation", r"relocat"),
        ("background_check", r"background_check|drug_(test|screen)"),
        ("criminal_record", r"criminal|convict|felony"),
        ("prior_employment", r"worked_for|previously_employed|former_employee"),
        ("non_compete", r"non_compete|non_solicit"),
    ]
    return [(kind, None) for kind, rx in rules if re.search(rx, k)][:1]


def home_jurisdiction(answers: dict[str, str]) -> str:
    return jurisdiction_code(answers.get("country_of_residence", "")) or HOME_DEFAULT


def typed_facts(answers: dict[str, str], profile_values: dict[str, str]) -> list[Fact]:
    """The typed facts among the stored answers, plus the profile's total years."""
    home = home_jurisdiction(answers)
    out = [Fact(key, kind, value, scope) for key, value in answers.items()
           for kind, scope in fact_kinds(key, value, home)]
    if profile_values.get("years_of_experience") and not any(f.kind == "total_years" for f in out):
        out.append(Fact("years_of_experience", "total_years", profile_values["years_of_experience"]))
    return out


# ── Resolution ────────────────────────────────────────────────────────────────

_YES = {"yes", "y", "true", "i agree", "agree", "i do", "i am", "i will", "i have"}
_NO = {"no", "n", "false", "i do not", "i don t", "i am not", "i will not", "i have not"}
_INVERTED_RE = re.compile(r"without (the )?(need (for|of) |requiring |needing |any |requiring any )?(a |an |the |"
                          r"employer |company |visa |immigration |work )*(visa |employer |company )?sponsor"
                          r"|(not|never) (require|need)|no (visa )?sponsorship")
_NEGATION_RE = re.compile(r"\b(not|never|no longer)\b|n't\b")
_AUX_RE = re.compile(r"^\W*(do|does|did|are|is|am|have|has|had|will|would|can|could|were|was|may|shall|should)\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"
                      r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4}\b", re.I)
_THRESHOLD_RE = re.compile(r"(?:at least|minimum of|min\.?|more than|over)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:\+|or more|plus)")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def yes_no(value: str) -> str | None:
    """'yes' / 'no' when a value is a plain yes or no ("Yes", "No, I am a US citizen"), else None."""
    v = _norm(value)
    for word, group in (("yes", _YES), ("no", _NO)):
        if v in group or any(v.startswith(g + " ") for g in group if len(g) > 1) or v.split(" ")[0] == word:
            return word
    return None


def is_yes_no_question(label: str, options: list[str]) -> bool:
    opts = {_norm(o) for o in options if _norm(o)}
    if opts:
        return bool(opts) and all(yes_no(o) for o in opts) and len({yes_no(o) for o in opts}) == 2
    return bool(_AUX_RE.search(label.casefold()))


def _number(value: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", value.replace(",", ""))
    return float(m.group().replace(",", ".")) if m else None


def _range(option: str) -> tuple[float, float] | None:
    """(low, high) of a numeric option: "3-5", "5+", "Less than 1", "10 or more years", "4"."""
    o = option.casefold().replace(",", "")
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", o)]
    if not nums:
        return None
    if re.search(r"less than|under|fewer than|below|up to|<", o):
        return (0.0, nums[0] - (0 if "up to" in o else 1e-9))
    if re.search(r"\+|or more|more than|over|at least|plus|>", o):
        return (nums[0] + (1e-9 if re.search(r"more than|over|>", o) else 0), float("inf"))
    if len(nums) >= 2:
        return (nums[0], nums[1])
    return (nums[0], nums[0])


def pick_range(number: float, options: list[str]) -> str | None:
    """The one numeric option whose range holds number; None if none or several do."""
    hits = [o for o in options if (r := _range(o)) and r[0] <= number <= r[1]]
    if len(hits) > 1:  # "1-3" / "3-5" both hold 3: prefer the one starting at it
        starts = [o for o in hits if _range(o)[0] == number]
        hits = starts if len(starts) == 1 else hits
    return hits[0] if len(hits) == 1 else None


@dataclass
class Resolution:
    """What code decided for a screening question.

    value set        -> answered in code from `basis` (value may be a stored value or derived from it)
    candidates set   -> a constrained model call may answer using only these facts
    neither          -> no fact backs the question (NeedsAnswer / skip)
    """
    kinds: set[str]
    value: str | None = None
    basis: list[str] = field(default_factory=list)
    derived: bool = False             # value differs from the stored value (flipped, threshold, range)
    candidates: list[Fact] = field(default_factory=list)
    missing: str = ""                 # why nothing backs it (logged)


def _scopes(kind: str, label: str, job_scope: str | None, home: str) -> list[str | None]:
    if kind in JURISDICTIONAL:
        named = question_jurisdictions(label)
        if named:
            return list(dict.fromkeys(named))
        return [home if job_scope is None else job_scope]
    if kind == "skill_years":
        return [_skill_norm(p) for p in skill_phrases(label)]
    return [None]


def _matches(fact: Fact, kind: str, scope: str | None) -> bool:
    if fact.kind != kind:
        return False
    if kind == "skill_years":  # "python" answers "python", "python 3" and "python programming"
        fact_words, want = set(fact.scope.split()), set((scope or "").split())
        return bool(fact_words) and fact_words <= want
    return kind not in JURISDICTIONAL or fact.scope == scope


def resolve(label: str, field_type: str, options: list[str], kinds: set[str], facts: list[Fact],
            *, job_scope: str | None, home: str, preferred: str | None = None) -> Resolution:
    """Answer a screening question from typed facts in code, or say which facts may answer it."""
    res = Resolution(kinds=set(kinds))
    needed: list[tuple[str, str | None]] = [(k, s) for k in sorted(kinds) for s in _scopes(k, label, job_scope, home)]
    per_need: list[list[Fact]] = []
    for kind, scope in needed:
        if scope == AMBIGUOUS:
            res.missing = f"{kind}: the job's country is ambiguous"
            return res
        hits = [f for f in facts if _matches(f, kind, scope)]
        if not hits:
            res.missing = f"no stored {kind}" + (f" for {scope}" if scope else "")
            return res
        per_need.append(hits)
    candidates = list(dict.fromkeys(f for hits in per_need for f in hits))
    res.candidates = candidates
    yn_question = is_yes_no_question(label, options)
    text = " ".join(label.casefold().split())

    # Years: compare numbers in code (threshold questions, range options, plain numbers).
    if kinds <= {"skill_years", "total_years"} and all(_number(f.value) is not None for f in candidates):
        chosen = [hits[0] for hits in per_need]
        numbers = [_number(f.value) for f in chosen]
        basis = [f.key for f in chosen]
        threshold = _THRESHOLD_RE.search(text)
        if yn_question and threshold:
            need = float(next(g for g in threshold.groups() if g))
            return _derived(res, "Yes" if all(n >= need for n in numbers) else "No", basis, options)
        if len(chosen) == 1 and not yn_question:
            if options:
                pick = pick_range(numbers[0], options)
                return _derived(res, pick, basis, options) if pick else res
            res.value, res.basis = chosen[0].value, basis
            return res
        return res

    if len(kinds) > 1 or len(per_need) > 1:
        return res  # compound question ("authorized ... without sponsorship?"): the model combines facts
    hits = per_need[0]
    fact = next((f for f in hits if f.key == preferred), None)
    if fact is None and yn_question:
        fact = next((f for f in hits if yes_no(f.value)), None)
    if fact is None and options:
        fact = next((f for f in hits if any(_norm(f.value) == _norm(o) or _norm(o) in _norm(f.value)
                                            for o in options if _norm(o))), None)
    if fact is None and len(hits) == 1:
        fact = hits[0]
    if fact is None:
        return res
    answer = yes_no(fact.value)
    if yn_question and kinds == {"sponsorship"} and _INVERTED_RE.search(text):
        if answer is None:
            return res
        return _derived(res, "No" if answer == "yes" else "Yes", [fact.key], options)
    if yn_question and (answer is None or _NEGATION_RE.search(text)):
        return res  # e.g. "Are you a protected veteran?" with "Decline to self-identify": the model decides
    if not yn_question and _norm(fact.value) in ("yes", "no"):
        return res  # a bare yes/no doesn't answer "Relocation preferences" as asked: the model decides
    if field_type == "date" and not _DATE_RE.search(fact.value):
        res.candidates, res.missing = [], f"no stored date for {fact.kind} ({fact.key} is not a date)"
        return res
    if field_type == "number":
        number = _number(fact.value)
        if number is None:
            return res
        return _derived(res, f"{number:g}", [fact.key], [])
    res.value, res.basis = fact.value, [fact.key]
    return res


def _derived(res: Resolution, value: str | None, basis: list[str], options: list[str]) -> Resolution:
    if value is not None and options:  # yes/no options as the form words them ("Yes, I do")
        word = yes_no(value)
        value = next((o for o in options if word and yes_no(o) == word), value)
    res.value, res.basis, res.derived = value, basis, True
    return res
