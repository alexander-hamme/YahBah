"""derive_outcome() and allowed_actions(): the contract tables (docs/wiki/contracts/run-lifecycle.md §7, §8)."""
from datetime import datetime, timezone
from typing import get_args

import pytest

from yahbah.run_state import IN_FLIGHT, TERMINAL, RunStatus, allowed_actions, derive_outcome

NOW = datetime.now(timezone.utc)


def test_every_status_is_either_in_flight_or_terminal():
    assert IN_FLIGHT | TERMINAL == set(get_args(RunStatus))
    assert not IN_FLIGHT & TERMINAL


@pytest.mark.parametrize("status, mode, confirmed, outcome", [
    ("PENDING", "review", None, "queued"),
    ("RUNNING", "review", None, "in_progress"),
    ("AWAITING_REVIEW", "review", None, "awaiting_review"),
    ("COMPLETED", "testing", None, "test_only"),
    ("COMPLETED", "testing", NOW, "test_only"),        # a test run is never a submission
    ("COMPLETED", "review", NOW, "email_verified"),
    ("COMPLETED", "review", None, "submitted"),
    ("COMPLETED", None, None, "submitted"),             # legacy (pre-contract) live run
    ("SUBMIT_UNCERTAIN", "review", None, "submit_uncertain"),
    ("FAILED", "review", None, "failed"),
    ("CANCELLED", "review", None, "cancelled"),
    ("EXPIRED", "review", None, "expired"),
    ("DUPLICATE", "review", None, "duplicate"),
])
def test_derive_outcome(status, mode, confirmed, outcome):
    assert derive_outcome(status, mode, confirmed) == outcome


@pytest.mark.parametrize("status, mode, attempted, actions", [
    ("PENDING", "review", None, ["cancel"]),
    ("RUNNING", "review", None, ["cancel"]),
    ("AWAITING_REVIEW", "review", None, ["approve", "reject", "cancel"]),
    ("FAILED", "review", None, ["retry", "delete"]),
    ("CANCELLED", "review", None, ["retry", "delete"]),
    ("EXPIRED", "review", None, ["retry", "delete"]),
    ("DUPLICATE", "review", None, ["retry", "delete"]),
    ("FAILED", None, NOW, ["delete"]),                  # legacy: attempted, so never retried
    ("COMPLETED", "testing", None, ["retry", "delete"]),
    ("COMPLETED", "review", NOW, ["delete"]),
    ("SUBMIT_UNCERTAIN", "review", NOW, ["confirm_submitted", "confirm_not_submitted"]),
])
def test_allowed_actions(status, mode, attempted, actions):
    assert allowed_actions(status, mode, attempted) == actions
