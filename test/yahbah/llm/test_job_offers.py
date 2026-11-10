"""Job offers (contract C9, yahbah.llm.job_offers): the code checks on what the metadata extractor says a
posting offers, and the extraction that feeds them (the real activity code with the model call scripted).

The checks prove that each quote's words are on the page, not what they mean: a negated remote quote with
value "yes" passes code (the verifier has to catch it; see test_a_negated_remote_quote_passes_code)."""
import copy

import pytest
from loguru import logger

from yahbah.llm.job_offers import MAX_LOCATIONS, MAX_SALARY_BANDS, amounts, validate_offers

DESCRIPTION = """Senior Data Engineer
Offices in Boston, New York City and Denver. This is a hybrid role: three days a week in the office.
Remote work is not available for this position.
This is a full-time position.
We are unable to sponsor visas for this role.
Pay: $150,000 - $180,000 per year in New York; $130K\u2013$160K per year in Denver.
"""

UNKNOWN = {"value": "unknown", "quote": None}


def offers(**overrides) -> dict:
    """A raw extractor reply in the C9 shape, everything grounded in DESCRIPTION."""
    raw = {
        "locations": [{"place": "Boston", "quote": "Offices in Boston, New York City and Denver"},
                      {"place": "Denver", "quote": "Offices in Boston, New York City and Denver"}],
        "remote": {"value": "no", "quote": "Remote work is not available"},
        "work_arrangement": {"value": "hybrid", "quote": "This is a hybrid role"},
        "employment_type": {"value": "full_time", "quote": "This is a full-time position."},
        "visa_sponsorship": {"value": "not_offered", "quote": "We are unable to sponsor visas"},
        "salary_bands": [{"min": 150000, "max": 180000, "currency": "USD", "period": "year", "location": "New York",
                          "quote": "$150,000 - $180,000 per year in New York"}],
    }
    raw.update(overrides)
    return raw


@pytest.fixture
def logs():
    records: list[str] = []
    handler = logger.add(lambda m: records.append(str(m)), level="DEBUG", format="{message}")
    yield records
    logger.remove(handler)


def test_grounded_offers_pass_unchanged() -> None:
    raw = offers()
    assert validate_offers(copy.deepcopy(raw), DESCRIPTION) == raw


def test_a_quote_not_on_the_page_is_dropped_and_logged_by_field_name_only(logs) -> None:
    out = validate_offers(offers(
        locations=[{"place": "Austin", "quote": "Offices in Austin"},
                   {"place": "Boston", "quote": "Offices in Boston, New York City and Denver"}],
        remote={"value": "yes", "quote": "Fully remote, work from anywhere"},
        salary_bands=[{"min": 200000, "max": 250000, "currency": "USD", "period": "year", "location": None,
                       "quote": "$200,000 - $250,000 per year"}]), DESCRIPTION)
    assert out["locations"] == [{"place": "Boston", "quote": "Offices in Boston, New York City and Denver"}]
    assert out["remote"] == UNKNOWN
    assert out["salary_bands"] == []
    assert any("locations[0]" in r for r in logs) and any("remote" in r for r in logs)
    assert any("salary_bands[0]" in r for r in logs)
    # Never the text: no place, quote or number reaches the log.
    assert not any(s in r for r in logs for s in ("Austin", "Fully remote", "200,000", "anywhere"))


def test_quotes_match_across_whitespace_case_and_typographic_dashes() -> None:
    out = validate_offers(offers(
        remote={"value": "no", "quote": "remote work  is NOT\navailable"},
        salary_bands=[{"min": 130000, "max": 160000, "currency": "USD", "period": "year", "location": "Denver",
                       "quote": "$130K-$160K per year in Denver"}]), DESCRIPTION)
    assert out["remote"] == {"value": "no", "quote": "remote work  is NOT\navailable"}
    assert [b["location"] for b in out["salary_bands"]] == ["Denver"]


def test_quotes_must_match_whole_words() -> None:
    out = validate_offers(offers(locations=[{"place": "Boston", "quote": "fices in Boston"}],
                                 remote={"value": "no", "quote": "emote work is not available"}), DESCRIPTION)
    assert out["locations"] == [] and out["remote"] == UNKNOWN


def test_a_negated_remote_quote_passes_code() -> None:
    """Documented limit: the words are on the page, the meaning is the verifier's to judge."""
    out = validate_offers(offers(remote={"value": "yes", "quote": "Remote work is not available"}), DESCRIPTION)
    assert out["remote"] == {"value": "yes", "quote": "Remote work is not available"}


def test_a_band_paired_with_a_location_its_quote_does_not_name_is_dropped() -> None:
    out = validate_offers(offers(salary_bands=[
        {"min": 150000, "max": 180000, "currency": "USD", "period": "year", "location": "Denver",
         "quote": "$150,000 - $180,000 per year in New York"},
        {"min": 130000, "max": 160000, "currency": "USD", "period": "year", "location": "Denver",
         "quote": "$130K\u2013$160K per year in Denver"}]), DESCRIPTION)
    assert [(b["min"], b["location"]) for b in out["salary_bands"]] == [(130000, "Denver")]


def test_band_numbers_not_in_the_quote_are_dropped() -> None:
    def band(lo, hi):
        return {"min": lo, "max": hi, "currency": "USD", "period": "year", "location": None,
                "quote": "$150,000 - $180,000 per year in New York"}

    for lo, hi in [(160000, 180000), (150000, 190000), (150, 180), (None, None), (180000, 150000), (-150000, None)]:
        assert validate_offers(offers(salary_bands=[band(lo, hi)]), DESCRIPTION)["salary_bands"] == [], (lo, hi)
    assert validate_offers(offers(salary_bands=[band(150000, None)]), DESCRIPTION)["salary_bands"][0]["max"] is None


@pytest.mark.parametrize("text, expected", [
    ("$150,000", {150000}),
    ("150k", {150000}),
    ("150K\u2013180K", {150000, 180000}),
    ("$150-180k", {150, 150000, 180000}),
    ("$150 to $180K", {150, 150000, 180000}),
    ("USD 150,000.00 - 180,000.00", {150000, 180000}),
    ("$45.50/hour", {45.5}),
    ("1.2M", {1200000}),
    ("3 months, 40 hours", {3, 40}),
])
def test_amounts_read_currency_thousands_and_k_forms(text: str, expected: set) -> None:
    assert amounts(text) == expected


def test_k_and_comma_forms_ground_a_band() -> None:
    description = "Base pay: 150k-180k USD per year. Contract rate $45.50/hour. Senior band $200,000+ annually."
    out = validate_offers(offers(locations=[], remote=UNKNOWN, work_arrangement=UNKNOWN, employment_type=UNKNOWN,
                                 visa_sponsorship=UNKNOWN, salary_bands=[
        {"min": 150000.0, "max": 180000.0, "currency": "usd", "period": "year", "location": None,
         "quote": "150k-180k USD per year"},
        {"min": 45.5, "max": 45.5, "currency": "$", "period": "hour", "location": None,
         "quote": "Contract rate $45.50/hour"},
        {"min": 200000, "max": None, "currency": None, "period": None, "location": None,
         "quote": "$200,000+ annually"}]), description)
    assert out["salary_bands"] == [
        {"min": 150000, "max": 180000, "currency": "USD", "period": "year", "location": None,
         "quote": "150k-180k USD per year"},
        {"min": 45.5, "max": 45.5, "currency": None, "period": "hour", "location": None,
         "quote": "Contract rate $45.50/hour"},
        {"min": 200000, "max": None, "currency": None, "period": None, "location": None,
         "quote": "$200,000+ annually"}]
    assert isinstance(out["salary_bands"][0]["min"], int)


def test_a_place_must_appear_in_its_own_quote() -> None:
    out = validate_offers(offers(locations=[{"place": "Seattle", "quote": "Offices in Boston, New York City and Denver"},
                                            {"place": "New York City",
                                             "quote": "Offices in Boston, New York City and Denver"}]), DESCRIPTION)
    assert [loc["place"] for loc in out["locations"]] == ["New York City"]


def test_limits() -> None:
    cities = [f"City{i}" for i in range(12)]
    description = "Offices in " + ", ".join(cities) + ". " + " ".join(
        f"${100 + i}k in City{i}." for i in range(7)) + " Remote: " + "x" * 300
    quote = "Offices in " + ", ".join(cities)
    out = validate_offers({
        "locations": [{"place": c, "quote": quote} for c in cities],
        "salary_bands": [{"min": (100 + i) * 1000, "max": None, "currency": "USD", "period": "year",
                          "location": f"City{i}", "quote": f"${100 + i}k in City{i}"} for i in range(7)],
        "remote": {"value": "yes", "quote": "Remote: " + "x" * 300},
    }, description)
    assert [loc["place"] for loc in out["locations"]] == cities[:MAX_LOCATIONS]
    assert len(out["salary_bands"]) == MAX_SALARY_BANDS
    assert out["remote"] == UNKNOWN  # quote over 200 characters

    long_place = "A" * 61
    bad_places = [long_place, "One Two Three Four Five Six Seven", "Remote: yes", "Boston\nDenver", "", "   "]
    text = "Offices: " + " ".join(bad_places) + " and Boston"
    out = validate_offers({"locations": [{"place": p, "quote": text[:200]} for p in bad_places]
                           + [{"place": " Boston ", "quote": "and Boston"}, {"place": "boston", "quote": "and Boston"}]},
                          text)
    assert out["locations"] == [{"place": "Boston", "quote": "and Boston"}]  # trimmed, deduped


def test_enum_values_need_a_quote_and_a_known_value() -> None:
    out = validate_offers(offers(
        remote={"value": "yes", "quote": None},
        work_arrangement={"value": "sometimes", "quote": "This is a hybrid role"},
        employment_type={"value": "unknown", "quote": "This is a full-time position."},
        visa_sponsorship={"value": "offered", "quote": ""}), DESCRIPTION)
    assert out["remote"] == out["work_arrangement"] == out["employment_type"] == out["visa_sponsorship"] == UNKNOWN


def test_band_period_and_location_shape() -> None:
    def band(**kw):
        return {"min": 150000, "max": 180000, "currency": "USD", "period": "year", "location": None,
                "quote": "$150,000 - $180,000 per year in New York"} | kw

    assert validate_offers(offers(salary_bands=[band(period="week")]), DESCRIPTION)["salary_bands"] == []
    assert validate_offers(offers(salary_bands=[band(location="New York: NY")]), DESCRIPTION)["salary_bands"] == []
    assert validate_offers(offers(salary_bands=[band(location="New York")]), DESCRIPTION)["salary_bands"] == [
        band(location="New York")]


def test_nothing_surviving_is_none() -> None:
    assert validate_offers(offers(), "") is None
    assert validate_offers({}, DESCRIPTION) is None
    assert validate_offers(offers(remote=UNKNOWN, work_arrangement=UNKNOWN, employment_type=UNKNOWN,
                                  visa_sponsorship=UNKNOWN, locations=[{"place": "Austin", "quote": "Austin"}],
                                  salary_bands=[]), DESCRIPTION) is None


def test_malformed_parts_are_dropped_not_raised() -> None:
    out = validate_offers({"locations": ["Boston", {"place": 3, "quote": "Boston"}, None],
                           "remote": "yes", "work_arrangement": None,
                           "salary_bands": [{"min": "150000", "quote": "$150,000"}, 7],
                           "employment_type": {"value": "full_time", "quote": "This is a full-time position."}},
                          DESCRIPTION)
    assert out == {"locations": [], "remote": UNKNOWN, "work_arrangement": UNKNOWN,
                   "employment_type": {"value": "full_time", "quote": "This is a full-time position."},
                   "visa_sponsorship": UNKNOWN, "salary_bands": []}


# ── Extraction: the job_metadata call carries the offers; the activity returns them validated ─────────

async def test_the_metadata_call_returns_validated_offers(llm, activity_env) -> None:
    from yahbah.schemas import JobMetadataInput
    from yahbah.workflows.activities import llm as module

    raw = offers(remote={"value": "yes", "quote": "Work from anywhere"})  # not on the page
    llm.on("driver", "_JobMetadata", reply={"title": "Senior Data Engineer", "location": "Boston, MA", "offers": raw})
    llm.on("driver", "_JobTechExtraction", reply={"technologies": ["Python"], "specialties": []})

    out = await activity_env(workflow_id="application-r1").call(
        module.extract_job_metadata_activity, JobMetadataInput(run_id="r1", job_description=DESCRIPTION))

    assert (out.title, out.location) == ("Senior Data Engineer", "Boston, MA")
    assert out.job_offers == offers(remote=UNKNOWN)
    [call] = llm.calls_for(schema="_JobMetadata")
    schema = call.schema.model_json_schema()
    assert "offers" in schema["required"]  # always decoded, so "unknown" is said rather than left out
    assert "Job posting text:" in call.user and DESCRIPTION in call.user


async def test_no_offers_in_the_reply_is_none(llm, activity_env) -> None:
    from yahbah.schemas import JobMetadataInput
    from yahbah.workflows.activities import llm as module

    llm.on("driver", "_JobMetadata", reply={"title": "Senior Data Engineer"})
    llm.on("driver", "_JobTechExtraction", reply={"technologies": [], "specialties": []})
    out = await activity_env(workflow_id="application-r1").call(
        module.extract_job_metadata_activity, JobMetadataInput(run_id="r1", job_description=DESCRIPTION))
    assert out.job_offers is None and out.title == "Senior Data Engineer"


async def test_extract_job_offers_is_one_validated_metadata_call(llm) -> None:
    from yahbah.llm.client import OllamaClient
    from yahbah.workflows.activities.llm import extract_job_offers

    llm.on("driver", "_JobMetadata", reply={"offers": offers()})
    assert await extract_job_offers(OllamaClient("driver"), DESCRIPTION) == offers()
    assert [c.schema_name for c in llm.calls] == ["_JobMetadata"]
    assert await extract_job_offers(OllamaClient("driver"), "too short") is None
    assert len(llm.calls) == 1
