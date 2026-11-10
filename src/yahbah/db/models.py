import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, JSON, String, Text, Boolean, Float, text,
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
    industry: Mapped[str | None] = mapped_column(String)
    company_description: Mapped[str | None] = mapped_column(String)
    work_model: Mapped[str | None] = mapped_column(String)
    seniority_level: Mapped[str | None] = mapped_column(String)
    experience_required: Mapped[str | None] = mapped_column(String)
    posted_date: Mapped[str | None] = mapped_column(String)
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
    # yahbah.run_state.RunStatus (docs/wiki/contracts/run-lifecycle.md §1); written only by the run's
    # own workflow activities, fenced on temporal_workflow_id (workflows/activities/db_ops.py)
    current_state: Mapped[str | None] = mapped_column(String)
    # workflow stage: QUEUED | STARTING | DISCOVER | MAP_FORM | EXTRACT_METADATA | CHECK_DUPLICATE
    # | MAP_ANSWERS | GENERATE_COVER_LETTER | FILL | REVIEW | SUBMIT | DONE (workflows/application.py)
    failure_reason: Mapped[str | None] = mapped_column(String)  # run_state.FailureReason; every terminal status but COMPLETED
    submission_mode: Mapped[str | None] = mapped_column(String)  # run_state.SubmissionMode at creation; NULL = legacy
    origin: Mapped[str | None] = mapped_column(String)  # run_state.Origin: manual | discovered
    review_round: Mapped[int | None] = mapped_column(Integer)  # 1, 2, ... per entry into AWAITING_REVIEW
    review_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application_run.id", ondelete="SET NULL")
    )
    temporal_workflow_id: Mapped[str | None] = mapped_column(String)
    # The attempt lease (contract §7): (workflow, activity sequence, attempt) of the activity attempt that
    # acts on the run now. Taken at every activity start (workflows/activities/lease.py), never by an
    # older attempt; fenced writes and the submission claim require it.
    lease_workflow_id: Mapped[str | None] = mapped_column(String)
    lease_activity_seq: Mapped[int | None] = mapped_column(Integer)
    lease_attempt: Mapped[int | None] = mapped_column(Integer)
    account_email: Mapped[str | None] = mapped_column(String)
    account_password: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    match_score: Mapped[int | None] = mapped_column()
    match_rationale: Mapped[str | None] = mapped_column(Text)
    tracking_url: Mapped[str | None] = mapped_column(String)
    confirmation_html: Mapped[str | None] = mapped_column(Text)
    # Set with the submission claim, right BEFORE the first submit click (run_state.claim_submission),
    # or by the agent tripwire (run_state.record_possible_submission). Set and unverified = SUBMIT_UNCERTAIN.
    submit_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A confirmation email ("thank you for applying") arrived at this run's alias.
    email_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_confirmed_message_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # One active run per posting (contract §7)
        Index("uq_application_run_active_posting", "job_posting_id", unique=True,
              postgresql_where=text("status IN ('PENDING', 'RUNNING', 'AWAITING_REVIEW')")),
    )

    job_posting: Mapped[JobPosting] = relationship(back_populates="runs")
    steps: Mapped[list["ApplicationStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ApplicationStep.created_at"
    )
    artifacts: Mapped[list["ApplicationArtifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ApplicationArtifact.created_at"
    )
    status_updates: Mapped[list["ApplicationStatusUpdate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ApplicationStatusUpdate.email_date"
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
    # screenshot | trace | cover_letter | form_schema | answer_plan | submitted_values | new_fields |
    # submit_evidence | confirmation | field_mappings (legacy: runs before the answer engine)
    path: Mapped[str] = mapped_column(String, nullable=False)
    artifact_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    run: Mapped[ApplicationRun] = relationship(back_populates="artifacts")


# ---------------------------------------------------------------------------
# application_status_update — tracks post-submission email status changes
# ---------------------------------------------------------------------------

class ApplicationStatusUpdate(Base):
    __tablename__ = "application_status_update"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application_run.id"), nullable=False
    )
    status_type: Mapped[str] = mapped_column(String, nullable=False)
    # UNDER_REVIEW | ONLINE_ASSESSMENT | INTERVIEW_REQUEST |
    # INTERVIEW_SCHEDULED | OFFER | REJECTED | WITHDRAWN | OTHER
    subject: Mapped[str | None] = mapped_column(String)
    sender: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)
    gmail_message_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    raw_snippet: Mapped[str | None] = mapped_column(Text)
    email_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    run: Mapped[ApplicationRun] = relationship(back_populates="status_updates")


# ---------------------------------------------------------------------------
# scheduled_job — jobs parsed from alert emails, awaiting promotion
# ---------------------------------------------------------------------------

class ScheduledJob(Base):
    __tablename__ = "scheduled_job"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_url: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    # "linkedin" | "indeed" | "glassdoor" | "greenhouse" | "other"
    title: Mapped[str | None] = mapped_column(String)
    company: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    match_score: Mapped[int | None] = mapped_column(Integer)
    match_rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="SCHEDULED")
    # SCHEDULED | APPROVED | SKIPPED | PROMOTED | EXPIRED
    hold: Mapped[bool] = mapped_column(Boolean, default=False)
    gmail_message_id: Mapped[str] = mapped_column(String, nullable=False)
    promote_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    promoted_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application_run.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_scheduled_job_status", "status"),
        Index("ix_scheduled_job_promote_after", "promote_after"),
    )


# ---------------------------------------------------------------------------
# submission_claim — one per posting that may have been submitted (contract §6)
# ---------------------------------------------------------------------------

class SubmissionClaim(Base):
    """Taken atomically with submit_attempted_at right before the first submit click
    (run_state.claim_submission). It outlives its run, so deleting a run never removes duplicate
    protection; only confirm-not-submitted releases it."""

    __tablename__ = "submission_claim"

    posting_key: Mapped[str] = mapped_column(String, primary_key=True)  # batch 1: str(job_posting_id)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application_run.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
