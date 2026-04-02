"""
Seeds (or updates) the applicant profile from config/personal.yaml.

    uv run python scripts/seed_profile.py
"""
import asyncio
import sys
from pathlib import Path

# Make src/ importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select
from yahbah.config import load_prompts_config
from yahbah.db.models import ApplicantProfile
from yahbah.db.session import AsyncSessionLocal


async def main() -> None:
    config = load_prompts_config()
    profile_data = config["profile"]

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(ApplicantProfile).limit(1))
        profile = existing.scalar_one_or_none()

        if profile is not None:
            print("Profile already exists — updating.")
            for k, v in profile_data.items():
                setattr(profile, k, v)
        else:
            print("Creating new profile.")
            profile = ApplicantProfile(**profile_data)
            session.add(profile)

        await session.commit()
        print(f"Profile saved: {profile.full_name} <{profile.email}>")


if __name__ == "__main__":
    asyncio.run(main())
