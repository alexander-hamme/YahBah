"""Runtime-setting validation and the effective settings runs are created with (contract §3, §8):
yahbah.config.validate_runtime_setting / effective_runtime_settings."""
import pytest

from yahbah.config import RUNTIME_SETTINGS, effective_runtime_settings, validate_runtime_setting


@pytest.mark.parametrize("key, value, stored", [
    ("submission_mode", "testing", "testing"),
    ("submission_mode", "review", "review"),
    ("review_window_hours", 0.5, 0.5),
    ("review_window_hours", 48, 48.0),
    ("job_alert_polling_enabled", True, True),
    ("job_alert_max_promotions_per_cycle", 0, 0),
    ("job_alert_backfill_since", "2026-04-16", "2026-04-16"),
])
def test_valid_values_are_stored(key, value, stored) -> None:
    assert validate_runtime_setting(key, value) == stored


@pytest.mark.parametrize("key, value", [
    ("submission_mode", "auto_reviewed"),  # batch 2
    ("submission_mode", "yolo"),
    ("submission_mode", None),
    ("review_window_hours", 0),
    ("review_window_hours", -1),
    ("review_window_hours", "48"),
    ("review_window_hours", True),
    ("job_alert_polling_enabled", "true"),
    ("job_alert_min_match_score", 50.5),
    ("job_alert_sender_patterns", "*@jobright.ai"),
    ("job_alert_backfill_since", "last week"),
    ("testing_mode", True),  # removed by the contract
    ("no_such_setting", 1),
])
def test_invalid_values_and_unknown_keys_are_refused(key, value) -> None:
    with pytest.raises(ValueError, match=key):
        validate_runtime_setting(key, value)


def test_every_key_in_the_shipped_settings_file_is_known_except_the_removed_one() -> None:
    import yaml

    from harness.env import REPO

    shipped = yaml.safe_load((REPO / "config" / "settings.yaml").read_text()) or {}
    assert set(shipped) - set(RUNTIME_SETTINGS) <= {"testing_mode"}


@pytest.mark.parametrize("raw, mode, window", [
    ({}, "review", 48.0),
    ({"submission_mode": "yolo", "review_window_hours": "soon"}, "review", 48.0),
    ({"submission_mode": "testing", "review_window_hours": 1.5}, "testing", 1.5),
    ({"testing_mode": True}, "review", 48.0),  # the old flag means nothing now
])
def test_effective_settings_fail_closed(raw, mode, window) -> None:
    effective = effective_runtime_settings(raw)
    assert (effective["submission_mode"], effective["review_window_hours"]) == (mode, window)
