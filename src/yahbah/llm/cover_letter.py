"""
Generates a tailored cover letter from the job description + applicant profile.
Output is plain text via OllamaClient.generate_text (no JSON enforcement).
"""
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import OllamaClient

_SYSTEM_PROMPT = """\
You are a professional cover letter writer. Write a concise, compelling cover letter
(3–4 paragraphs, under 400 words) tailored to the job description and the applicant's profile.

Guidelines:
- Address "Hiring Manager" (no specific name).
- Open with a strong hook connecting the applicant's background to the role.
- Highlight 2–3 relevant skills or experiences from the profile.
- Close with a clear call to action.
- Do NOT include date, address blocks, or "Sincerely" headers — plain body text only.
- Output the cover letter text directly, no JSON.
"""


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
                for exp in profile.work_experience[:2]
            ]
            profile_text += "Recent experience:\n" + "\n".join(exp_lines)

        user_prompt = (
            f"JOB DESCRIPTION:\n{job_description[:3000]}\n\n"
            f"APPLICANT PROFILE:\n{profile_text}"
        )

        return await self._client.generate_text(_SYSTEM_PROMPT, user_prompt)
