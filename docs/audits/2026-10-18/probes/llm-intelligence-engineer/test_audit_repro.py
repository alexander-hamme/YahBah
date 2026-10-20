"""Deterministic repros (models faked) of answer-correctness gaps in yahbah.llm.field_mapper."""
from types import SimpleNamespace

import pytest

from yahbah.llm import field_mapper as fm
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer, _FallbackAnswer, _KeyMapping, _MappingResponse
from yahbah.schemas import FormField

PROFILE = SimpleNamespace(
    full_name="Jane Q Doe", email="jane@example.com", phone="+1 555 0100", location="Boston, MA",
    linkedin_url=None, github_url=None, portfolio_url=None, years_of_experience=7, skills=["Python"],
    work_experience=[{"title": "Engineer", "company": "Acme", "duration": "2019 - now",
                      "summary": "Built a Kafka ingestion service handling 2B events/day."}],
    education=[], bio=None, resume_path="/files/resume.pdf",
)
STORED = {"willing_to_relocate": "Yes, willing to relocate", "sponsorship_required": "No"}


class Fake:
    def __init__(self, *r):
        self.r, self.prompts = list(r), []

    async def generate_structured(self, system_prompt, user_prompt, response_model, images=None):
        self.prompts.append(user_prompt)
        return self.r.pop(0)


@pytest.fixture
def env(monkeypatch):
    store: dict[str, str] = {}

    async def cached(label, options):
        return store.get(fm._question_key(label, options))

    async def put(label, options, answer):
        store.setdefault(fm._question_key(label, options), answer)

    monkeypatch.setattr(fm, "_cached_answer", cached)
    monkeypatch.setattr(fm, "_store_answer", put)
    monkeypatch.setattr(fm, "_all_known_answers", lambda: dict(STORED))
    monkeypatch.setattr(fm, "_build_system_prompt", lambda: "system")
    monkeypatch.setattr(fm, "load_prompts_config", lambda: {"prompts": {"fallback_answer": "x"}})
    return store


def mapper(source, *prose):
    m = FieldMapper()
    m._driver = Fake(_MappingResponse(mappings=[_KeyMapping(key="1", source=source, value="", confidence=1.0)]))
    m._prose = Fake(*prose)
    return m


def f(label, ftype="text", options=None):
    return FormField(key="1", label=label, field_type=ftype, required=True, options=options or [])


async def test_irrelevant_evidence_passes_the_honesty_check(env):
    """Live 2026-10-17 (artifacts/debug-20261017-001456): 'Available from' -> 'Immediately', backed only by
    the relocation stored answers. Expected NeedsAnswer; current code types it in and caches it."""
    guess = _FallbackAnswer(answer="Immediately", job_specific=False,
                            evidence=["willing_to_relocate: Yes, willing to relocate"])
    out = await mapper("custom", guess).map([f("Available from")], PROFILE)
    assert out[0].value == "Immediately"          # accepted as fact
    assert len(env) == 1                           # and cached for every future form


async def test_conditional_followup_cache_ignores_the_parent_answer(env):
    na = _FallbackAnswer(answer="N/A", job_specific=False, evidence=["sponsorship_required: No"])
    await mapper("custom", na).map([f("If yes, please provide details", "textarea")], PROFILE)
    # A different form where the parent question was answered "Yes": no model call, cached "N/A" reused.
    m = mapper("custom")
    out = await m.map([f("If yes, please provide details", "textarea")], PROFILE,
                      prior=[fm.FieldMapping("0", "Have you worked for us before?", "radio", "custom", "Yes", 1.0)])
    assert out[0].value == "N/A" and m._prose.prompts == []


async def test_cache_shadows_an_updated_stored_answer(env, monkeypatch):
    no = _FallbackAnswer(answer="No", job_specific=False, evidence=["sponsorship_required: No"])
    q = f("Will you need us to sponsor a work visa?", "radio", ["Yes", "No"])
    await mapper("custom", no).map([q], PROFILE)
    monkeypatch.setattr(fm, "_all_known_answers", lambda: {**STORED, "sponsorship_required": "Yes"})
    out = await mapper("custom").map([q], PROFILE)
    assert out[0].value == "No"                    # stale: the profile now says Yes


def test_profile_text_omits_work_experience_summaries():
    assert "Kafka" not in fm.profile_text(PROFILE)  # the prose model never sees the bullets
