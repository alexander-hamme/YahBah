"""
One-time script to seed the applicant profile into the database.
Edit the values below to match your real profile, then run:

    uv run python scripts/seed_profile.py
"""
import asyncio
import sys
from pathlib import Path

# Make src/ importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select
from yahbah.db.models import ApplicantProfile
from yahbah.db.session import AsyncSessionLocal


PROFILE = {
    "full_name": "Alexander Hamme",
    "email": "alexhamme96@gmail.com",
    "phone": "+1-857-264-7620",
    "location": "Boston, MA",
    "linkedin_url": "https://linkedin.com/in/alexander-hamme",
    "github_url": "https://github.com/alexander-hamme",
    "portfolio_url": None,
    "resume_path": "/Users/alex/Documents/YahBah/files/Alexander-Hamme-Resume.pdf",  # MUST be an absolute path
    "years_of_experience": 7,
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
    "work_experience": [
        {
            "title": "Senior Machine Learning Engineer",
            "company": "The Charles Stark Draper Laboratory",
            "duration": "Nov, 2023 - Present",
            "summary": \
"""
➢ Deployed agentic LLM-driven archival source-code intelligence platform with RAG, Knowledge Graphs, and custom hallucination reduction techniques, transforming decades of unstructured data into queryable knowledge (Neo4j, pgvector, vLLM)

➢ Engineered a scalable enterprise data monitoring and risk detection system, leveraged agentic LLMs, RAG, and Knowledge Graphs to automate risk detection and surface actionable insights to data teams (Neo4j, Weaviate, LangGraph, TensorRT)

➢ Directed core ML team to delivery of multilingual audio transcription pipeline with SOTA models; enabling fault-tolerant, real-time processing of streaming audio into searchable insights (PyTorch, Kubernetes, Kafka, Redis, ElasticSearch, Prometheus)
""",
        },
        {
            "title": "Machine Learning Engineer",
            "company": "The Charles Stark Draper Laboratory",
            "duration": "May, 2019 – Nov, 2023",
            "summary": \
"""
➢ Led bioinformatics team through high-throughput vaccine modeling & prediction deep-learning pipeline deployment; integrated AlphaFoldv2, PostgreSQL, Docker, AWS Batch + EventBridge; published to Govt. & secured follow-on ($740k to $3.9M)
 
➢ Embedded quantized deep-learning speech transcription models into Android ATAK framework to enable real-time voice streaming and command recognition, deployed to search & rescue teams in the field (DeepSpeech, Tensorflow /TFLite) 

➢ Delivered high-throughput data streaming pipeline to ingest multimodal biometric Big Datasets into Cassandra database; trained deep learning models to predict stress from feature extraction set (AWS Kinesis, Spark, Keras, NoSQL/Cassandra)
""",
        },
    ],
    "education": [
        {
            "degree": "B.S. Computer Science",
            "institution": "Bard College",
            "year": "2019",
            "gpa": "3.8",
        }
    ],
    "bio": (
        "Senior Machine Learning Engineer with seven years of experience delivering applied AI solutions to complex, "
        "real-world challenges for government R&D contracts ($50K–$5M+). I specialize in translating research code "
        "and models into robust, performance-critical systems. Now seeking opportunities to join a high-impact, "
        "public-facing team."
    ),
}


async def main() -> None:
    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(ApplicantProfile).limit(1))
        profile = existing.scalar_one_or_none()

        if profile is not None:
            print("Profile already exists — updating.")
            for k, v in PROFILE.items():
                setattr(profile, k, v)
        else:
            print("Creating new profile.")
            profile = ApplicantProfile(**PROFILE)
            session.add(profile)

        await session.commit()
        print(f"Profile saved: {profile.full_name} <{profile.email}>")


if __name__ == "__main__":
    asyncio.run(main())
