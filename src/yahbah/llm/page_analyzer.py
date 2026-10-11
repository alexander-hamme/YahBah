"""
PageAnalyzer — LLM calls for the generic form extraction agent.

Two operations:
  judge_form()    — decides if the real application form is visible and what to do next
  enhance_fields() — confirms labels, generates CSS selectors for raw DOM elements
"""
from typing import Literal

from pydantic import BaseModel

from yahbah.llm.client import OllamaClient
from yahbah.config import settings


def _load_prompts() -> dict:
    import yaml
    with open(settings.prompts_path) as f:
        return yaml.safe_load(f)["prompts"]


# ---------------------------------------------------------------------------
# Pydantic output schemas
# ---------------------------------------------------------------------------

class FormAction(BaseModel):
    type: Literal["click", "wait", "scroll", "type"]
    selector: str | None = None
    text: str | None = None
    description: str


class FormJudgment(BaseModel):
    is_ready: bool
    reasoning: str
    action: FormAction | None = None


class EnhancedField(BaseModel):
    original_index: int
    label: str
    field_type: str
    css_selector: str
    required: bool = False
    options: list[str] = []
    is_file_upload: bool = False
    skip: bool = False


class _EnhancedFieldsResponse(BaseModel):
    fields: list[EnhancedField]


# ---------------------------------------------------------------------------
# PageAnalyzer
# ---------------------------------------------------------------------------

class PageAnalyzer:
    def __init__(self, llm: OllamaClient | None = None) -> None:
        self._llm = llm or OllamaClient()
        self._prompts = _load_prompts()

    async def judge_form(
        self,
        snapshot: str,
        site_hints: str,
        actions_taken: list[str],
        iteration: int,
    ) -> FormJudgment:
        system = (
            self._prompts["generic_form_judge"]
            .replace("{site_hints}", site_hints or "No site-specific hints available.")
            .replace("{actions_taken}", "\n".join(f"- {a}" for a in actions_taken) or "None yet.")
            .replace("{iteration}", str(iteration))
        )
        user = f"Current page snapshot:\n{snapshot}"
        return await self._llm.generate_structured(system, user, FormJudgment)

    async def enhance_fields(
        self,
        raw_fields: list[dict],
        profile_keys: list[str],
    ) -> list[EnhancedField]:
        import json
        system = self._prompts["generic_form_analyzer"].replace(
            "{profile_keys}", ", ".join(profile_keys)
        )
        user = f"Raw DOM elements:\n{json.dumps(raw_fields, indent=2)}"
        result = await self._llm.generate_structured(system, user, _EnhancedFieldsResponse)
        return result.fields
