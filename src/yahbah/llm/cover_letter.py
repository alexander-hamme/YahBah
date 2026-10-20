"""
Generates a tailored cover letter from the job description + applicant profile, only for forms
that ask for one (`cover_letter_requested`). Plain text via OllamaClient.generate_text.

The style prompt (`cover_letter`) is the applicant's own (personal.yaml); the fixed grounding rules
(`cover_letter_rules`, prompts.yaml) are always appended. Like generated answers, the letter may
not state a number the profile doesn't contain: one rewrite, then those sentences are dropped.
"""
from loguru import logger

from yahbah.config import load_prompts_config
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import OllamaClient
from yahbah.llm.grounding import drop_sentences, unsupported_numbers
from yahbah.llm.profile import profile_text


def cover_letter_requested(field_mappings: list[dict]) -> bool:
    """Whether the mapped form asks for a cover letter (a field mapped to the cover_letter source)."""
    return any(m.get("source") == "cover_letter" for m in field_mappings)


class CoverLetterGenerator:
    def __init__(self) -> None:
        self._client = OllamaClient("prose")

    async def generate(self, job_description: str, profile: ApplicantProfile) -> str:
        applicant = profile_text(profile, contact=False)
        user_prompt = f"JOB DESCRIPTION:\n{job_description}\n\nAPPLICANT PROFILE:\n{applicant}"
        prompts = load_prompts_config()["prompts"]
        system_prompt = f"{prompts['cover_letter'].rstrip()}\n\n{prompts['cover_letter_rules']}"
        text = await self._client.generate_text(system_prompt, user_prompt)

        extra = unsupported_numbers(text, applicant)
        if extra:
            logger.warning(f"[cover_letter] states numbers not in the profile ({sorted(extra)}): rewriting")
            text = await self._client.generate_text(
                system_prompt,
                f"{user_prompt}\n\nA previous draft stated numbers the profile doesn't contain "
                f"({', '.join(sorted(extra))}). Write the letter again without them.")
            if extra := unsupported_numbers(text, applicant):
                text = "\n\n".join(p for p in (drop_sentences(par, extra) for par in text.split("\n\n")) if p)
        return text
