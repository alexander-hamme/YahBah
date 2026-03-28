"""
Generates a tailored cover letter from the job description + applicant profile.
Output is plain text (not JSON).
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

    async def generate(
        self, job_description: str, profile: ApplicantProfile
    ) -> str:
        profile_text = f"""
Name: {profile.full_name}
Years of experience: {profile.years_of_experience}
Skills: {', '.join(profile.skills)}
Bio: {profile.bio or 'Not provided'}
"""
        # Work experience summary (top 2 roles)
        if profile.work_experience:
            exp_lines = []
            for exp in profile.work_experience[:2]:
                exp_lines.append(
                    f"  - {exp.get('title', '')} at {exp.get('company', '')} "
                    f"({exp.get('duration', '')}): {exp.get('summary', '')}"
                )
            profile_text += "Recent experience:\n" + "\n".join(exp_lines)

        user_prompt = (
            f"JOB DESCRIPTION:\n{job_description[:3000]}\n\n"
            f"APPLICANT PROFILE:\n{profile_text}"
        )

        # Plain text — no JSON schema enforcement
        client = OllamaClient()
        # Override format to plain text by calling _call_ollama with a patched payload
        return await self._generate_plain(client, user_prompt)

    async def _generate_plain(self, client: OllamaClient, user_prompt: str) -> str:
        """
        Cover letter uses plain text output — bypass JSON format flag.
        """
        import httpx
        from yahbah.config import settings

        payload = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.7},
            # No "format": "json" here
        }

        async with httpx.AsyncClient(timeout=settings.ollama_timeout) as http:
            resp = await http.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            resp.raise_for_status()

        return resp.json()["message"]["content"].strip()
