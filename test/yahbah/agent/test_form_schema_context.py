"""The context read_form_schema gives each field besides its label (agent/tools.py READ_FORM_SCHEMA_JS): helper text
(aria-describedby) as `description`, the nearest heading above it as `section`, and up to 40 native select options.
The answer engine reads short labels through them; map_form passes them on with "" when the page has none."""
import pytest

from harness.browser import load
from yahbah.agent.discover import _clean_schema_fields
from yahbah.agent.tools import get_form_schema

pytestmark = pytest.mark.browser


@pytest.fixture
async def schema(browser, fixture_server) -> dict[str, dict]:
    await load(browser, fixture_server.url("synthetic/schema_context.html"))
    return {f["label"]: f for f in await get_form_schema(browser)}


async def test_helper_text_tied_by_aria_describedby_is_the_field_description(schema) -> None:
    assert schema["First name"]["description"] == "As it appears on your passport."
    assert schema["Email"]["description"] == "We only use it for this application."  # the empty error slot adds nothing
    assert "description" not in schema["Preferred name"]


async def test_a_radio_group_takes_its_fieldsets_helper_text_else_its_first_members(schema) -> None:
    auth = schema["Are you authorized to work in the United States?"]
    assert auth["type"] == "radio" and auth["options"] == ["Yes", "No"]
    assert auth["description"] == "Without needing visa sponsorship now or in the future."
    assert schema["relocate"]["description"] == "The role is based in Boston, MA."


async def test_the_section_is_the_nearest_visible_heading_above_the_field(schema) -> None:
    assert schema["First name"]["section"] == "Personal information"
    assert schema["Preferred name"]["section"] == "Personal information"
    # a group's own legend is its label, not its section
    assert schema["Are you authorized to work in the United States?"]["section"] == "Work eligibility"
    # another fieldset's legend above a field heads only that fieldset
    assert schema["relocate"]["section"] == "Work eligibility"
    assert schema["Preferred city"]["section"] == "Relocation details"  # an enclosing fieldset's legend
    assert schema["Country of residence"]["section"] == "Work eligibility"  # the hidden <h2> is skipped
    # a legend that is the field's label is not its section
    assert schema["Resume/CV"]["type"] == "file" and schema["Resume/CV"]["section"] == "Documents"
    assert "section" not in schema["Search jobs"]  # above every heading


async def test_a_native_select_shows_up_to_40_options(schema) -> None:
    options = schema["Country of residence"]["options"]
    assert len(options) == 30
    assert options[0] == "Country 01" and options[-1] == "Country 30"


async def test_map_form_passes_the_context_on_and_defaults_it_to_empty(schema) -> None:
    cleaned = {f["label"]: f for f in _clean_schema_fields(list(schema.values()))}
    assert cleaned["First name"]["description"] == "As it appears on your passport."
    assert cleaned["First name"]["section"] == "Personal information"
    assert cleaned["Preferred name"]["description"] == ""
    assert cleaned["Search jobs"]["section"] == ""
