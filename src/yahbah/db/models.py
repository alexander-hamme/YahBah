import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime, ForeignKey, JSON, String, Text, Boolean, Float,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# job_posting
# ---------------------------------------------------------------------------

class JobPosting(Base):
    __tablename__ = "job_posting"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # Resolved URL after all redirects (may differ from url if submitted via LinkedIn etc.)
    canonical_url: Mapped[str | None] = mapped_column(String, index=True)
    title: Mapped[str | None] = mapped_column(String)
    company: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[int | None] = mapped_column()
    salary_max: Mapped[int | None] = mapped_column()
    technologies: Mapped[list[str] | None] = mapped_column(JSONB)
    specialties: Mapped[list[str] | None] = mapped_column(JSONB)
    company_website: Mapped[str | None] = mapped_column(String)
    ats_type: Mapped[str] = mapped_column(String, default="greenhouse")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    runs: Mapped[list["ApplicationRun"]] = relationship(
        back_populates="job_posting", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# application_run
# ---------------------------------------------------------------------------

class ApplicationRun(Base):
    __tablename__ = "application_run"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("job_posting.id"), nullable=False
    )
    job_url: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    # PENDING | RUNNING | COMPLETED | FAILED
    current_state: Mapped[str | None] = mapped_column(String)
    # mirrors workflow step names: OPEN_PAGE | EXTRACT_FORM | MAP_FIELDS | etc
    temporal_workflow_id: Mapped[str | None] = mapped_column(String)
    account_email: Mapped[str | None] = mapped_column(String)
    account_password: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job_posting: Mapped[JobPosting] = relationship(back_populates="runs")
    steps: Mapped[list["ApplicationStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ApplicationStep.created_at"
    )
    artifacts: Mapped[list["ApplicationArtifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ApplicationArtifact.created_at"
    )


# ---------------------------------------------------------------------------
# application_step
# ---------------------------------------------------------------------------

class ApplicationStep(Base):
    __tablename__ = "application_step"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application_run.id"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    # PENDING | RUNNING | COMPLETED | FAILED | SKIPPED
    logs: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    run: Mapped[ApplicationRun] = relationship(back_populates="steps")


# ---------------------------------------------------------------------------
# application_artifact
# ---------------------------------------------------------------------------

class ApplicationArtifact(Base):
    __tablename__ = "application_artifact"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application_run.id"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    # screenshot | trace | cover_letter | form_schema | field_mappings | confirmation
    path: Mapped[str] = mapped_column(String, nullable=False)
    artifact_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    run: Mapped[ApplicationRun] = relationship(back_populates="artifacts")


# ---------------------------------------------------------------------------
# applicant_profile
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# gmail_checkpoint — stores incremental sync state for Gmail ingestion
# ---------------------------------------------------------------------------

class GmailCheckpoint(Base):
    __tablename__ = "gmail_checkpoint"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    history_id: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# applicant_profile
# ---------------------------------------------------------------------------

class ApplicantProfile(Base):
    __tablename__ = "applicant_profile"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    linkedin_url: Mapped[str | None] = mapped_column(String)
    github_url: Mapped[str | None] = mapped_column(String)
    portfolio_url: Mapped[str | None] = mapped_column(String)
    resume_path: Mapped[str] = mapped_column(String, nullable=False)
    years_of_experience: Mapped[int] = mapped_column(default=0)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    work_experience: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    education: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    bio: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
