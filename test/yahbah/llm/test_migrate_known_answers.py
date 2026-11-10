"""scripts/migrate_known_answers.py: a synthetic personal.yaml's known_answers become a reviewable draft
facts: list that loads through Fact; personal.yaml itself is never written. Synthetic data only."""
import importlib.util

import pytest
import yaml

from harness.env import REPO
from yahbah.config import settings
from yahbah.llm.facts import Fact

LEGACY = """\
known_answers:
  country_of_residence: "United States"
  work_authorization: "Yes"
  work_authorization_germany: "No"
  sponsorship_required_eu: "Yes"
  work_status: "US Citizen"
  years_python: "6"
  gender_identity: "Decline to self-identify"
  start_date: "Within 2 weeks of an offer"
  zip_code_of_residence: "00000"
  2fa_device: "Phone"
  favourite_colour: "Green"
profile:
  full_name: "Jane Doe"
"""


@pytest.fixture
def migrate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("migrate_known_answers", REPO / "scripts" / "migrate_known_answers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    personal = tmp_path / "personal.yaml"
    personal.write_text(LEGACY)
    monkeypatch.setattr(settings, "personal_path", str(personal))
    return module


def draft_facts(tmp_path) -> dict[str, dict]:
    return {f["id"]: f for f in yaml.safe_load((tmp_path / "personal.facts-draft.yaml").read_text())["facts"]}


def test_known_answers_become_typed_draft_facts(migrate, tmp_path, capsys) -> None:
    before = (tmp_path / "personal.yaml").read_bytes()
    assert migrate.main([]) == 0
    facts = draft_facts(tmp_path)
    for fact in facts.values():
        Fact.model_validate(fact)  # every draft fact is valid as written

    auth = facts["work_authorization"]
    assert (auth["kind"], auth["data_type"], auth["value"], auth["jurisdiction"], auth["sensitivity"]) == (
        "work_authorization", "boolean", True, "US", "legal")
    assert auth["guidance"] == "current authorization in the United States only"
    assert (facts["work_authorization_germany"]["jurisdiction"], facts["work_authorization_germany"]["value"]) == (
        "DE", False)
    assert (facts["sponsorship_required_eu"]["kind"], facts["sponsorship_required_eu"]["jurisdiction"]) == (
        "sponsorship", "EU")
    status = facts["work_status"]
    assert (status["kind"], status["jurisdiction"], status["sensitivity"]) == ("citizenship", "US", "legal")
    years = facts["years_python"]
    assert (years["kind"], years["data_type"], years["value"], years["description"]) == (
        "skill_experience_years", "number", 6, "Years of professional python experience")
    assert (facts["gender_identity"]["kind"], facts["gender_identity"]["sensitivity"]) == ("eeo_gender", "eeo")
    assert facts["start_date"]["data_type"] == "relative_availability"
    assert "f_2fa_device" in facts  # ids start with a letter
    other = facts["favourite_colour"]
    assert (other["kind"], other["description"]) == ("unclassified", "Stored answer for: favourite colour")

    text = (tmp_path / "personal.facts-draft.yaml").read_text()
    assert "# REVIEW: kind guessed from the key name\n- id: favourite_colour" in text
    assert "# PLACEHOLDER: replace or delete\n- id: zip_code_of_residence" in text
    assert not text.split("- id: work_authorization\n")[0].splitlines()[-1].startswith("#")  # a known key: no flag

    out = capsys.readouterr().out
    assert "favourite_colour" in out and "zip_code_of_residence" in out
    assert "Green" not in out and "US Citizen" not in out  # ids and counts only, never values
    assert (tmp_path / "personal.yaml").read_bytes() == before  # never written


def test_an_existing_draft_is_not_overwritten_without_force(migrate, tmp_path) -> None:
    draft = tmp_path / "personal.facts-draft.yaml"
    draft.write_text("# my reviewed draft\n")
    assert migrate.main([]) == 1
    assert draft.read_text() == "# my reviewed draft\n"
    assert migrate.main(["--force"]) == 0
    assert "work_authorization" in draft_facts(tmp_path)


def test_nothing_to_migrate_without_known_answers(migrate, tmp_path, capsys) -> None:
    (tmp_path / "personal.yaml").write_text("facts: []\n")
    assert migrate.main([]) == 0
    assert "nothing to migrate" in capsys.readouterr().out
    assert not (tmp_path / "personal.facts-draft.yaml").exists()
