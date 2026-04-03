"""
Generates a tailored cover letter from the job description + applicant profile.
Output is plain text via OllamaClient.generate_text (no JSON enforcement).
"""
from yahbah.config import load_prompts_config
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import OllamaClient


class CoverLetterGenerator:
    def __init__(self) -> None:
        self._client = OllamaClient()

    async def generate(self, job_description: str, profile: ApplicantProfile) -> str:
        profile_text = (
            f"Name: {profile.full_name}\n"
            f"Years of experience: {profile.years_of_experience}\n"
            f"Skills: {', '.join(profile.skills)}\n"
            f"Bio: {profile.bio or 'Not provided'}\n"
        )

        if profile.work_experience:
            exp_lines = [
                f"  - {exp.get('title', '')} at {exp.get('company', '')}: {exp.get('summary', '')}"
                for exp in profile.work_experience
                if exp.get("use_in_custom_prompts", True)
            ]
            profile_text += "Recent experience:\n" + "\n".join(exp_lines)

        user_prompt = (
            f"JOB DESCRIPTION:\n{job_description}\n\n"
            f"APPLICANT PROFILE:\n{profile_text}"
        )

        system_prompt = load_prompts_config()["prompts"]["cover_letter"]
        return await self._client.generate_text(system_prompt, user_prompt)
