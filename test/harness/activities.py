"""Browser activities without Chrome, for activity-level tests of yahbah.workflows.activities.browser.

    from harness.activities import FakeBrowserSession, fill_input, submit_input, running_run

FakeBrowserSession stands in for browser-use's BrowserSession inside SessionRegistry (the `registry`
fixture of test/invariants/batch1_review installs it); fill_input / submit_input build the activity
inputs for a one-field form; running_run seeds a RUNNING run.
"""
from pathlib import Path
from types import SimpleNamespace

JOB_URL = "https://jobs.harness.example/acme/review-1"
FIELD = {"key": "1", "label": "First name", "field_type": "text", "required": True, "options": []}
MAPPING = {"key": "1", "label": "First name", "field_type": "text", "source": "first_name", "value": "Jane",
           "confidence": 1.0}


class FakeBrowserSession:
    """browser-use's BrowserSession inside SessionRegistry, without Chrome."""

    def __init__(self, browser_profile=None, **kwargs) -> None:
        self.browser_profile = browser_profile or SimpleNamespace(user_data_dir=None)
        self.killed = False
        self.url = ""

    async def start(self) -> None:
        pass

    async def navigate_to(self, url: str) -> None:
        self.url = url

    async def get_current_page_url(self) -> str:
        return self.url

    async def take_screenshot(self, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"png")

    async def kill(self) -> None:
        self.killed = True


def fill_input(run_id, **kw):
    from yahbah.schemas import FillInput

    return FillInput(run_id=str(run_id), job_url=JOB_URL,
                     form_schema_dict={"fields": [FIELD], "page_url": "", "board": "generic"},
                     field_mappings=[MAPPING], job_description="", cover_letter_path=None, **kw)


def submit_input(run_id):
    from yahbah.schemas import SubmitInput

    return SubmitInput(run_id=str(run_id), job_url=JOB_URL,
                       form_schema_dict={"fields": [FIELD], "page_url": "", "board": "generic"},
                       field_mappings=[MAPPING], written={"1": "Jane"}, job_description="",
                       cover_letter_path=None, cover_letter_text=None, account_email="jane+1@example.test")


async def running_run(db, **values):
    posting = await db.seed_posting()
    return posting, await db.seed_run(posting, status="RUNNING", current_state="FILL", **values)
