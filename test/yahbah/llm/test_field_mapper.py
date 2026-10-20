"""FieldMapper resolution rules: the real mapper with both models scripted (harness.llm) against the
synthetic applicant (harness.profile). The answer cache is an in-memory stand-in here; the real
table is tested in test_answer_cache.py."""
import pytest

from harness.llm import answer, map_all
from harness.profile import applicant
from yahbah.llm import answer_cache
from yahbah.llm import field_mapper as fm
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer
from yahbah.schemas import FormField

US_JOB = "Software Engineer, Boston, MA (United States). We build data pipelines in Python."
MUNICH_JOB = "Backend Engineer, Munich, Germany. Our platform team builds data services."


def field(key: str, label: str, ftype: str = "text", options=None, required: bool = True) -> FormField:
    return FormField(key=key, label=label, field_type=ftype, required=required, options=options or [])


def mapped(*rows: tuple[str, str] | tuple[str, str, str]) -> dict:
    """A driver mapping reply: (key, source[, value]) per field."""
    return {"mappings": [{"key": r[0], "source": r[1], "value": r[2] if len(r) > 2 else "", "confidence": 1.0}
                         for r in rows]}


@pytest.fixture
def cache(monkeypatch):
    """answer_cache in memory: {(namespace, label): (answer, basis)}."""
    rows: dict = {}

    async def lookup(namespace, label, options, **provenance):
        return rows.get((namespace, label))

    async def store(namespace, label, options, value, basis, **provenance):
        rows[(namespace, label)] = (value, basis)

    monkeypatch.setattr(answer_cache, "lookup", lookup)
    monkeypatch.setattr(answer_cache, "store", store)
    return rows


@pytest.fixture
def facts(applicant_facts, cache):
    return applicant_facts


# ── Code resolution ───────────────────────────────────────────────────────────

async def test_profile_and_stored_answers_are_resolved_in_code_with_their_basis(llm, facts) -> None:
    llm.on("driver", "MappingResponse", reply=mapped(("1", "first_name", "J."), ("2", "how_did_you_hear"),
                                                     ("3", "resume")))
    out = await FieldMapper().map([field("1", "First Name"), field("2", "Where did you find this posting?"),
                                   field("3", "Resume", "file")], applicant())
    assert [(x.source, x.value, x.basis, x.generated) for x in out] == [
        ("first_name", "Jane", ["first_name"], False), ("how_did_you_hear", "Job board", ["how_did_you_hear"], False),
        ("resume", "", [], False)]


async def test_autofill_tokens_are_mapped_in_code_without_the_model(llm, facts) -> None:
    fields = [field("1", "First Name"), field("2", "Last Name"), field("3", "E-mail address")]
    for f, token in zip(fields, ["given-name", "family-name", "email"]):
        f.autocomplete = token
    out = await FieldMapper().map(fields, applicant())
    assert [(x.source, x.value) for x in out] == [
        ("first_name", "Jane"), ("last_name", "Q Doe"), ("email", "jane.doe@example.test")]
    assert llm.calls == []


async def test_only_undecided_fields_go_to_the_model(llm, facts) -> None:
    llm.on("driver", "MappingResponse", reply=mapped(("2", "how_did_you_hear")))
    first = field("1", "First Name")
    first.autocomplete = "given-name"
    await FieldMapper().map([first, field("2", "How did you hear about us?")], applicant())
    sent = llm.calls_for("driver", "MappingResponse")[0].user
    assert '"key": "2"' in sent and '"key": "1"' not in sent  # fields go in read_form_schema's JSON shape


async def test_a_model_echoing_labels_as_keys_is_still_matched(llm, facts, monkeypatch) -> None:
    async def echo(self, system_prompt, user_prompt, response_model, images=None):
        return fm._MappingResponse(mappings=[fm._KeyMapping(key="First Name", source="first_name", confidence=1.0)])
    monkeypatch.setattr(fm.OllamaClient, "generate_structured", echo)
    out = await FieldMapper().map([field("1", "First Name")], applicant())
    assert (out[0].source, out[0].value) == ("first_name", "Jane")


def test_reply_schema_constrains_keys_and_sources() -> None:
    schema = fm._response_model(["1", "2"], ["email", "skip"]).model_json_schema()
    item = next(iter(schema["$defs"].values()))
    assert item["properties"]["key"]["enum"] == ["1", "2"]
    assert item["properties"]["source"]["enum"] == ["email", "skip"]


async def test_file_fields_only_ever_get_our_documents(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    with pytest.raises(NeedsAnswer):  # a required upload we have no document for
        await FieldMapper().map([field("1", "Portfolio", "file")], applicant())
    out = await FieldMapper().map([field("1", "Portfolio", "file", required=False)], applicant())
    assert out[0].source == "skip"
    assert llm.calls_for("prose") == []


async def test_required_fields_are_never_skipped_and_optional_skips_are_kept(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("skip"))
    llm.on("prose", reply=answer("Job board", basis=["how_did_you_hear"]))
    out = await FieldMapper().map([field("1", "How did you hear about this opening?"),
                                   field("2", "Preferred name", required=False)], applicant())
    assert [(x.source, x.value, x.basis) for x in out] == [
        ("custom", "Job board", ["how_did_you_hear"]), ("skip", "", [])]


async def test_a_composite_profile_value_must_be_found_in_that_field(llm, facts) -> None:
    llm.on("driver", "MappingResponse", reply=mapped(("1", "work_experience", "Acme Data"),
                                                     ("2", "work_experience", "Globex"), ("3", "resume")))
    llm.on("prose", handler=lambda call: answer("", insufficient_info=True))
    out = await FieldMapper().map([field("1", "Current employer"), field("2", "Previous employer", required=False),
                                   field("3", "Resume (paste)", "textarea", required=False)], applicant())
    assert (out[0].source, out[0].value, out[0].basis) == ("work_experience", "Acme Data", ["work_experience[0]"])
    assert out[1].source == "skip"  # "Globex" is nowhere in the profile: asked of the prose model, which can't
    assert out[2].source == "skip"  # no resume in a text box


# ── Screening facts ───────────────────────────────────────────────────────────

YN = ["Yes", "No"]


async def test_screening_facts_are_answered_in_code_from_typed_stored_facts(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    out = await FieldMapper().map([
        field("1", "Are you legally authorized to work in the United States?", "radio", YN),
        field("2", "Will you now or in the future require visa sponsorship?", "radio", YN),
        field("3", "Are you able to work in the US without visa sponsorship?", "radio", ["Yes, I can", "No, I can't"]),
        field("4", "Desired salary"),
    ], applicant(), job_description=US_JOB)
    assert [(x.value, x.basis) for x in out] == [
        ("Yes", ["work_authorization"]), ("No", ["sponsorship_required"]),
        ("Yes, I can", ["sponsorship_required"]), ("$150,000", ["salary_expectation"])]
    assert [x.source for x in out] == ["work_authorization", "sponsorship_required", "custom", "salary_expectation"]
    assert llm.calls_for("prose") == [] and len(llm.calls) == 1


@pytest.mark.parametrize("label, jd, job_location", [
    ("Are you legally authorized to work in Germany?", US_JOB, None),
    ("Will you now or in the future require visa sponsorship to work in the EU?", US_JOB, None),
    ("Will you require visa sponsorship?", MUNICH_JOB, None),
    ("Will you require visa sponsorship?", US_JOB, "Munich, Germany"),  # the posting's location wins
    ("Do you hold an active security clearance?", "All candidates must hold an active TS/SCI clearance.", None),
    ("When can you start?", US_JOB, None),
    ("How many years of experience do you have with TypeScript?", US_JOB, None),
    ("Are you at least 18 years of age?", US_JOB, None),
    ("Gender", US_JOB, None),
])
async def test_a_screening_question_without_a_typed_fact_needs_an_answer(llm, facts, label, jd, job_location) -> None:
    llm.on("driver", "MappingResponse", handler=map_all(lambda key: "work_authorization"))
    with pytest.raises(NeedsAnswer) as exc:
        await FieldMapper().map([field("1", label, "radio", YN)], applicant(), job_description=jd,
                                job_location=job_location)
    assert exc.value.label == label
    assert llm.calls_for("prose") == [] and llm.calls_for("driver", "ScreeningAnswer") == []
    out = await FieldMapper().map([field("1", label, "radio", YN, required=False)], applicant(), job_description=jd,
                                  job_location=job_location)
    assert (out[0].source, out[0].value) == ("skip", "")


async def test_other_jurisdictions_are_answered_from_their_own_keys(llm, facts) -> None:
    facts.update(work_authorization_germany="No", sponsorship_required_de="Yes")
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    out = await FieldMapper().map([field("1", "Are you legally authorized to work in Germany?", "radio", YN),
                                   field("2", "Will you require visa sponsorship?", "radio", YN)],
                                  applicant(), job_description=MUNICH_JOB)
    assert [(x.value, x.basis) for x in out] == [("No", ["work_authorization_germany"]),
                                                 ("Yes", ["sponsorship_required_de"])]


async def test_years_questions_compare_numbers_in_code(llm, facts) -> None:
    facts.update(years_python="6")
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    out = await FieldMapper().map([
        field("1", "How many years of experience do you have with Python?"),
        field("2", "Do you have 5+ years of professional experience with Python?", "radio", YN),
        field("3", "Years of Python experience", "select", ["0-2", "3-5", "6-8", "9+"]),
        field("4", "Do you have 8+ years of Python experience?", "radio", YN),
        field("5", "How many years of professional experience do you have?", "number"),
    ], applicant(), job_description=US_JOB)
    assert [(x.value, x.basis) for x in out] == [
        ("6", ["years_python"]), ("Yes", ["years_python"]), ("6-8", ["years_python"]), ("No", ["years_python"]),
        ("7", ["years_of_experience"])]
    with pytest.raises(NeedsAnswer):  # one of the two skills has no stored years
        await FieldMapper().map([field("1", "Do you have 5+ years of experience with Python and React?", "radio", YN)],
                                applicant(), job_description=US_JOB)


async def test_a_compound_question_is_derived_by_the_driver_from_only_its_facts(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("driver", "ScreeningAnswer", reply={"answer": "Yes", "basis": ["work_authorization", "sponsorship_required",
                                                                           "salary_expectation"],
                                               "insufficient_info": False})
    label = "Are you legally authorized to work in the United States without sponsorship?"
    out = await FieldMapper().map([field("1", label, "radio", YN)], applicant(), job_description=US_JOB)
    assert (out[0].source, out[0].value, out[0].basis) == ("custom", "Yes", ["sponsorship_required",
                                                                             "work_authorization"])
    call = llm.calls_for("driver", "ScreeningAnswer")[0]
    assert "salary" not in call.user and "Acme" not in call.user  # only the question's own facts are shown
    keys = call.schema.model_fields["basis"].annotation.__args__[0].__args__
    assert set(keys) == {"work_authorization", "sponsorship_required", "work_status"}


async def test_a_derived_screening_answer_needs_a_cited_fact(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("driver", "ScreeningAnswer", replies=[{"answer": "Yes", "basis": [], "insufficient_info": False},
                                                 {"answer": "", "basis": [], "insufficient_info": True}])
    question = field("1", "Are you a US citizen?", "radio", YN)
    for _ in range(2):
        with pytest.raises(NeedsAnswer):
            await FieldMapper().map([question], applicant(), job_description=US_JOB)


# ── Custom questions ──────────────────────────────────────────────────────────

async def test_open_questions_are_generated_from_the_profile_and_labelled(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", reply=answer("At Acme Data I built a Kafka ingestion service handling 2B events/day.",
                                 basis=["work_experience[0]"], generated=True))
    out = await FieldMapper().map([field("1", "Tell us about a time you developed something end to end", "textarea")],
                                  applicant(), job_description=US_JOB)
    assert (out[0].source, out[0].generated, out[0].basis) == ("custom", True, ["work_experience[0]"])
    call = llm.calls_for("prose")[0]
    assert "Kafka ingestion service" in call.user  # the experience bullets are in the facts
    assert "JOB (context only" in call.user and "pipelines in Python" in call.user
    assert "work_experience[0]" in call.schema.model_fields["basis"].annotation.__args__[0].__args__


async def test_a_generated_answer_may_not_introduce_numbers(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", replies=[
        answer("I cut costs by 40%. I built a Kafka ingestion service handling 2B events/day.", generated=True),
        answer("I cut costs by 40% at Acme Data.", generated=True),
    ])
    out = await FieldMapper().map([field("1", "What is your proudest achievement?", "textarea")], applicant())
    assert out[0].value == "I built a Kafka ingestion service handling 2B events/day."
    assert "40" in llm.calls_for("prose")[1].user  # the rewrite was told which numbers to drop


async def test_a_fact_question_answer_must_cite_and_match_a_fact(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", replies=[answer("Yes", basis=[]),                                 # material, no basis
                             answer("Immediately", basis=["willing_to_relocate"])])   # basis doesn't hold it
    with pytest.raises(NeedsAnswer):
        await FieldMapper().map([field("1", "Do you have a valid driver's license?", "radio", YN)], applicant())
    with pytest.raises(NeedsAnswer):
        await FieldMapper().map([field("1", "What is your preferred interview time?")], applicant())
    schema = llm.calls_for("prose")[0].schema
    assert "generated" not in schema.model_fields  # a fact question can't claim to be prose
    assert "JOB (context only" not in llm.calls_for("prose")[0].user


async def test_an_unbacked_answer_to_an_open_question_is_labelled_generated(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", reply=answer("Building reliable data systems.", basis=[]))
    out = await FieldMapper().map([field("1", "What motivates you?")], applicant())
    assert (out[0].value, out[0].generated) == ("Building reliable data systems.", True)


async def test_a_value_copied_from_its_basis_is_a_fact_not_prose(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", reply=answer("Example State University", basis=["education_school"], generated=True))
    out = await FieldMapper().map([field("1", "Which university did you attend?")], applicant())
    assert (out[0].value, out[0].basis, out[0].generated) == ("Example State University", ["education_school"], False)


async def test_a_conditional_follow_up_after_no_is_backed_by_the_parent_answer(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", reply=answer("N/A"))
    out = await FieldMapper().map([field("1", "Will you require visa sponsorship?", "radio", YN),
                                   field("2", "If yes, please explain", "textarea")], applicant(),
                                  job_description=US_JOB)
    assert (out[1].value, out[1].basis, out[1].generated) == ("N/A", ["sponsorship_required"], False)
    assert "Will you require visa sponsorship?: No" in llm.calls_for("prose")[0].user


# ── Cache ─────────────────────────────────────────────────────────────────────

async def test_only_grounded_generic_facts_are_cached(llm, facts, cache) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    replies = {
        "Do you have experience with PostgreSQL?": answer("Yes", basis=["skills"]),
        "Why do you want to join Acme Robotics?": answer("Acme Robotics builds the data tooling I enjoy.",
                                                         generated=True),
        "Describe your experience with data pipelines": answer("I led the move from cron jobs to Airflow.",
                                                               basis=["work_experience[0]"], generated=True),
        "Which of our values resonates with you?": answer("Ownership, as when I led the Airflow migration.",
                                                          basis=["work_experience[0]"], job_specific=True),
    }
    llm.on("prose", handler=lambda call: replies[call.user.split("\n", 1)[0].removeprefix("QUESTION: ")])
    fields = [field(str(i), label, "radio" if label.startswith("Do you") else "textarea")
              for i, label in enumerate(replies)]
    for f in fields:
        f.options = YN if f.field_type == "radio" else []
    await FieldMapper().map(fields, applicant(), job_description="Acme Robotics is hiring in Boston, MA.")
    assert cache == {("live", "Do you have experience with PostgreSQL?"): ("Yes", ["skills"])}

    llm.calls.clear()
    out = await FieldMapper(cache_namespace=answer_cache.DEBUG).map(fields[:1], applicant())
    assert out[0].value == "Yes" and len(llm.calls_for("prose")) == 1  # another namespace: not shared
    assert ("debug", "Do you have experience with PostgreSQL?") in cache


def test_cacheable_labels() -> None:
    assert answer_cache.cacheable("Do you have experience with PostgreSQL?")
    for label in ("If yes, please explain", "Please specify", "Other", "Details", "Why do you want to work here?",
                  "What excites you about this role?", "How did you hear about us?"):
        assert not answer_cache.cacheable(label), label
    assert not answer_cache.cacheable("Have you used Snowflake Cortex before?", "We build on Snowflake Cortex.")


# ── Freshness ─────────────────────────────────────────────────────────────────

async def test_an_edited_stored_answer_applies_without_a_restart(llm, facts) -> None:
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    question = field("1", "Will you now or in the future require visa sponsorship?", "radio", YN)
    assert (await FieldMapper().map([question], applicant(), job_description=US_JOB))[0].value == "No"
    text = facts.path.read_text().replace("sponsorship_required: 'No'", "sponsorship_required: 'Yes'")
    assert "sponsorship_required: 'Yes'" in text
    facts.path.write_text(text)  # edited on disk only: no cache_clear, no new process
    assert (await FieldMapper().map([question], applicant(), job_description=US_JOB))[0].value == "Yes"

