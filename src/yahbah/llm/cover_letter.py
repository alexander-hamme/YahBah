"""
Generates a tailored cover letter from the job description + applicant profile.
Output is plain text via OllamaClient.generate_text (no JSON enforcement).
"""
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import OllamaClient

_SYSTEM_PROMPT = \
("You are a professional cover letter writer. Use the following template EXACTLY:\
\
Dear <COMPANY> Hiring Team,\
\
I am a Senior Machine Learning Engineer at Draper Laboratory, with 7 years of experience in complex real-world domains. \
At Draper, I specialize in bringing promising research models all the way forward to highly-scalable, \
performance-optimized deployment.\
\
My recent work <RECENT WORK OPTIONS, SEE BELOW>. <CONCISE LAST LINE ABOUT MY BACKGROUND & EXPERIENCE, TYING SUBTLY TO \
THE JOB DESCRIPTION>.\
\
As a senior engineer, I’m also frequently responsible for working across multi-discipline teams to translate ambiguous \
customer goals into effective end-to-end products, which must operate reliably and accurately under real-world constraints. \
\
I believe my experience <10 words or less: PHRASE ABOUT BUILDING technologies/products/ML solutions IN A PHRASING RELEVANT \
TO JOB POST> aligns strongly with <COMPANY SPECIFIC GOAL  PRODUCT INITIATIVE OR AI STRATEGY RELEVANT TO THIS ROLE>. \
I would be thrilled to <10 words or less: Description of the impact you want to contribute to in this role, aligned \
with the goal/mission/initiative outlined in the job description>.\
\
I am confident that I would be a valuable addition to your team, and I am excited to discuss how I can contribute to <COMPANY\
BUSINESS OUTCOMES>.\
\
Thank you for your time, and I look forward to connecting.\
\
Sincerely, \
Alexander Hamme")

"""

- Replace ONLY the parts within angle < > brackets.  Try to match the tone of the rest of the letter, keeping it
 short and sweet, with a hint of technical but Very concise, and without being redundant or rambling.

 
 For RECENT WORK:
 
 If the Job is focused on Generative AI, LLMs, RAG, etc, use something along these lines, tuning it *SLIGHTLY* to 
 align with the job requirements and description:
 
 "My recent work has included successful product deliveries of large-scale agentic and RAG-enhanced LLM frameworks, 
 including a real-time enterprise data-risks platform and an archival intelligence pipeline with custom 
 GraphRAG and LLM Uncertainty techniques. These deployments span the full data processing and AI model lifecycle, from 
 optimizing inference and latency to integrating the models into production platforms (Kubernetes, Weaviate, Neo4j, 
  AWS, LangGraph, SQL/NoSQL)."
 
 Otherwise if the Job is more focused on general machine learning / AI Products, use something along these lines,
  tuning it *SLIGHTLY* to align with the job requirements and description:
 
"My recent work has included successful product deliveries across performance-critical
domains such as real-time audio processing, autonomous navigation, and large-scale
geospatial analytics. These deployments span the data processing + ML model lifecycle,
from training and optimizing models all the way to integration into production platforms
(Pytorch/Tensorflow, CUDA/Slurm, Docker, REST, AWS, SQL/NoSQL)."
 
- The final letter should be a smooth read, without any strange formatting, bullets, or typos. 
Don't use extra long dashes, or any dashes at all.  After filling in the blanks, remove any asterisks, 
markdown syntax, etc.
 
- Finally, Output the full cover letter text directly, no JSON.
"""



# """\
# You are a professional cover letter writer. Write a concise, compelling cover letter
# (3–4 paragraphs, under 400 words) tailored to the job description and the applicant's profile.
#
# Guidelines:
# - Opening address: If the company or team involved is small, tight-knit and/or the job description includes sentiments that imply they value a sense of family or cohesion, use "Dear [Company]",
# otherwise play it safe with "Dear [Company] Recruiting Team"  (Make sure to replace [Company] with the actual company name).
# - Open with a strong hook about the applican's bio and connect the applicant's background to the role.
# - Highlight 2–3 relevant recent experiences from the profile in 2-3 sentences.
# - highlight leadership, collaboration with team members and stakeholders, and expertise across a wide range of technologies.
# - Close with a clear connection between the applicant's experience and the specific job requirements/roles, followed by a clear call to action,
# how the applicant is excited to do <RELEVANT THING> with the company..
# - Output the cover letter text directly, no JSON.
# """


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
