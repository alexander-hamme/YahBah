"""
How well the applicant matches a job (22-99%), from the profile summary, the stored salary
expectation and the job description: one driver call.
"""
from pydantic import BaseModel

from yahbah.config import load_prompts_config
from yahbah.llm.client import OllamaClient
from yahbah.llm.facts import yaml_fact_value

__all__ = ["compute_match_score"]


class _MatchScoreResponse(BaseModel):
    match_score: int
    match_rationale: str


async def compute_match_score(profile_summary: str, job_description: str) -> tuple[int, str]:
    """Score how well the applicant matches the job (22-99%)."""
    salary_info = yaml_fact_value("salary_expectation") or "Not specified"
    user_prompt = (
        f"APPLICANT PROFILE:\n{profile_summary}\n\n"
        f"APPLICANT SALARY EXPECTATION: {salary_info}\n\n"
        f"JOB DESCRIPTION:\n{job_description}"
    )
    result = await OllamaClient("driver").generate_structured(
        system_prompt=load_prompts_config()["prompts"]["match_score"],
        user_prompt=user_prompt,
        response_model=_MatchScoreResponse,
    )
    return max(22, min(99, result.match_score)), result.match_rationale
