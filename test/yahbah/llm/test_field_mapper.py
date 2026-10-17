"""FieldMapper resolution rules, with the models and the answer cache faked."""
from types import SimpleNamespace

import pytest

from yahbah.llm import field_mapper as fm
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer, _FallbackAnswer, _KeyMapping, _MappingResponse
from yahbah.schemas import FormField


class FakeClient:
    """Returns queued responses in order; records the prompts it got."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def generate_structured(self, system_prompt, user_prompt, response_model, images=None):
        self.prompts.append(user_prompt)
        return self.responses.pop(0)


PROFILE = SimpleNamespace(
    full_name="Jane Q Doe", email="jane@example.com", phone="+1 555 0100", location="Boston, MA",
    linkedin_url="https://linkedin.com/in/jane", github_url=None, portfolio_url=None,
    years_of_experience=7, skills=["Python"], work_experience=[], education=[], bio=None,
    resume_path="/files/resume.pdf",
)


@pytest.fixture
def cache(monkeypatch):
    store: dict[str, str] = {}

    async def cached(label, options):
        return store.get(label)

    async def put(label, options, answer):
        store[label] = answer

    monkeypatch.setattr(fm, "_cached_answer", cached)
    monkeypatch.setattr(fm, "_store_answer", put)
    monkeypatch.setattr(fm, "_all_known_answers", lambda: {"salary_expectation": "$150,000"})
    monkeypatch.setattr(fm, "_build_system_prompt", lambda: "system")
    return store


def mapper(driver_mappings: list[dict], *prose: _FallbackAnswer) -> FieldMapper:
    m = FieldMapper()
    m._driver = FakeClient(_MappingResponse(mappings=[_KeyMapping(**d) for d in driver_mappings]))
    m._prose = FakeClient(*prose)
    return m


def field(key: str, label: str, ftype: str = "text", required: bool = True, options=None) -> FormField:
    return FormField(key=key, label=label, field_type=ftype, required=required, options=options or [])


async def test_profile_and_known_answers_are_resolved_in_code(cache) -> None:
    m = mapper([
        {"key": "1", "source": "first_name", "value": "J.", "confidence": 1.0},  # model's value ignored
        {"key": "2", "source": "salary_expectation", "value": "", "confidence": 1.0},
        {"key": "3", "source": "resume", "value": "", "confidence": 1.0},
    ])
    out = await m.map([field("1", "First Name"), field("2", "Desired salary"), field("3", "Resume", "file")], PROFILE)
    assert [(x.source, x.value) for x in out] == [
        ("first_name", "Jane"), ("salary_expectation", "$150,000"), ("resume", "")]


async def test_custom_questions_go_to_prose_and_generic_answers_are_cached(cache) -> None:
    m = mapper(
        [{"key": "1", "source": "custom", "value": "", "confidence": 1.0},
         {"key": "2", "source": "custom", "value": "", "confidence": 1.0}],
        _FallbackAnswer(answer="Yes", evidence=["Boston, MA"], insufficient_info=False, job_specific=False),
        _FallbackAnswer(answer="Your mission fits my work.", evidence=["Python"], insufficient_info=False, job_specific=True),
    )
    out = await m.map([field("1", "Willing to relocate?", "select", options=["Yes", "No"]),
                       field("2", "Why us?", "textarea")], PROFILE, job_description="JD")
    assert [x.value for x in out] == ["Yes", "Your mission fits my work."]
    assert cache == {"Willing to relocate?": "Yes"}  # the job-specific answer is not cached
    assert "OPTIONS: ['Yes', 'No']" in m._prose.prompts[0]


async def test_cached_answer_skips_the_model(cache) -> None:
    cache["Willing to relocate?"] = "No"
    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}])  # no prose responses queued
    out = await m.map([field("1", "Willing to relocate?")], PROFILE)
    assert out[0].value == "No"
    assert m._prose.prompts == []


async def test_required_fields_are_never_skipped(cache) -> None:
    m = mapper(
        [{"key": "1", "source": "skip", "value": "", "confidence": 0.9}],
        _FallbackAnswer(answer="Job board", evidence=["Jane Q Doe"], job_specific=False),
    )
    out = await m.map([field("1", "How did you hear about us?")], PROFILE)
    assert (out[0].source, out[0].value) == ("custom", "Job board")


async def test_unmapped_fields_default_to_custom_if_required_else_skip(cache) -> None:
    m = mapper([], _FallbackAnswer(answer="N/A", evidence=["Boston"], job_specific=False))
    out = await m.map([field("1", "Anything else?", required=True), field("2", "Pronouns", required=False)], PROFILE)
    assert [(x.source, x.value) for x in out] == [("custom", "N/A"), ("skip", "")]


async def test_low_confidence_required_profile_mapping_is_answered_by_prose(cache) -> None:
    m = mapper(
        [{"key": "1", "source": "location", "value": "", "confidence": 0.4}],
        _FallbackAnswer(answer="Boston, MA (open to relocation)", evidence=["Boston, MA"], job_specific=True),
    )
    out = await m.map([field("1", "Where are you based and would you relocate?")], PROFILE)
    assert out[0].source == "custom"


async def test_missing_personal_fact_fails_required_and_skips_optional(cache) -> None:
    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}],
               _FallbackAnswer(answer="", insufficient_info=True))
    with pytest.raises(NeedsAnswer) as exc:
        await m.map([field("1", "Passport number")], PROFILE)
    assert exc.value.label == "Passport number"

    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}],
               _FallbackAnswer(answer="", insufficient_info=True))
    out = await m.map([field("1", "Reference phone", required=False)], PROFILE)
    assert (out[0].source, out[0].value) == ("skip", "")


async def test_autofill_tokens_are_mapped_in_code_without_the_model(cache) -> None:
    m = mapper([])
    fields = [field("1", "First Name"), field("2", "Last Name"), field("3", "E-mail address")]
    for f, token in zip(fields, ["given-name", "family-name", "email"]):
        f.autocomplete = token
    out = await m.map(fields, PROFILE)
    assert [(x.source, x.value) for x in out] == [
        ("first_name", "Jane"), ("last_name", "Q Doe"), ("email", "jane@example.com")]
    assert m._driver.prompts == []  # no model call at all


async def test_only_undecided_fields_go_to_the_model(cache) -> None:
    m = mapper([{"key": "2", "source": "salary_expectation", "value": "", "confidence": 1.0}])
    first = field("1", "First Name")
    first.autocomplete = "given-name"
    await m.map([first, field("2", "Expected salary")], PROFILE)
    sent = m._driver.prompts[0]
    assert '"key": "2"' in sent and '"key": "1"' not in sent  # fields go in read_form_schema's JSON shape


async def test_a_model_echoing_labels_as_keys_is_still_matched(cache) -> None:
    m = mapper([{"key": "First Name", "source": "first_name", "value": "", "confidence": 1.0}])
    out = await m.map([field("1", "First Name")], PROFILE)
    assert (out[0].source, out[0].value) == ("first_name", "Jane")


async def test_file_fields_only_ever_get_our_documents(cache) -> None:
    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}])
    with pytest.raises(NeedsAnswer):  # a required upload we have no document for
        await m.map([field("1", "Portfolio", "file")], PROFILE)
    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}])
    out = await m.map([field("1", "Portfolio", "file", required=False)], PROFILE)
    assert out[0].source == "skip"
    assert m._prose.prompts == []  # never sent to the prose model


def test_reply_schema_constrains_keys_and_sources() -> None:
    schema = fm._response_model(["1", "2"], ["email", "skip"]).model_json_schema()
    item = next(iter(schema["$defs"].values()))
    assert item["properties"]["key"]["enum"] == ["1", "2"]
    assert item["properties"]["source"]["enum"] == ["email", "skip"]


async def test_resume_into_a_text_box_becomes_a_custom_answer(cache) -> None:
    m = mapper([{"key": "1", "source": "resume", "value": "", "confidence": 1.0}],
               _FallbackAnswer(answer="Please see my LinkedIn profile.", evidence=["linkedin.com/in/jane"], job_specific=False))
    out = await m.map([field("1", "Resume (paste)", "textarea")], PROFILE)
    assert out[0].source == "custom"


async def test_answers_without_verifiable_evidence_are_treated_as_guesses(cache) -> None:
    """Live run 4: "Available from" -> "Immediately" with nothing in the profile backing it."""
    guess = _FallbackAnswer(answer="Immediately", evidence=["available immediately"], job_specific=False)
    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}], guess)
    with pytest.raises(NeedsAnswer):  # the quote isn't in the material: a guess, never typed in
        await m.map([field("1", "Available from")], PROFILE)
    assert cache == {}  # and never cached

    m = mapper([{"key": "1", "source": "custom", "value": "", "confidence": 1.0}],
               _FallbackAnswer(answer="Immediately", evidence=[], job_specific=False))
    out = await m.map([field("1", "Available from", required=False)], PROFILE)
    assert (out[0].source, out[0].value) == ("skip", "")
