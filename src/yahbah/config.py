from pathlib import Path

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (src/yahbah/config.py → ../..). Relative config paths resolve against it, not the
# process's working directory, so the API and worker read the same files wherever they start.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://localhost/yahbah"

    # Temporal
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "job-applications"

    # Ollama — two models stay loaded together (~71 GB). Each model must always be
    # requested with the same num_ctx, or Ollama reloads it (25-30 s).
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout: int = 900  # seconds — local models need long read timeouts
    ollama_keep_alive: str = "30m"
    # Driver: agent loops, DOM/HTML work, mapping JSON, page judging (text only, fast MoE)
    model_driver: str = "qwen3-coder-next:latest"
    ctx_driver: int = 40960
    # Prose + vision + escalation: cover letters, free-text answers, screenshots, thinking
    model_prose_vision: str = "qwen3.8:27b"
    ctx_prose_vision: int = 32768

    # Browser agent
    max_concurrent_runs: int = 1
    agent_max_steps: int = 15
    submit_max_attempts: int = 4
    browser_headless: bool = True

    # Safety
    min_field_confidence: float = 0.7

    # Idempotency — block re-application to the same job within this window
    duplicate_window_days: int = 31

    # Gmail ingestion
    gmail_credentials_dir: str = "~/.config/yahbah/gmail"
    gmail_poll_interval_seconds: int = 7
    gmail_poll_timeout_seconds: int = 300  # 5 minutes
    gmail_label_parsed: str = "YahBah/Parsed"

    # Gmail status polling
    gmail_status_polling_interval_minutes: int = 10
    gmail_folder_label: str = "YahBah"
    gmail_status_label: str = "YahBah/Status"
    gmail_status_resync_days: int = 7
    # Per-status-type auto-archive toggles: True = move from inbox to
    # YahBah folder, False = leave in inbox for immediate visibility.
    gmail_auto_archive: dict[str, bool] = {
        "UNDER_REVIEW": True,
        "ONLINE_ASSESSMENT": False,
        "INTERVIEW_REQUEST": False,
        "INTERVIEW_SCHEDULED": False,
        "OFFER": False,
        "REJECTED": True,
        "WITHDRAWN": True,
        "OTHER": True,
    }

    # Job alert parsing
    job_alert_polling_interval_minutes: int = 30
    job_alert_label: str = "YahBah/Alerts"
    job_alert_promotion_delay_hours: int = 24
    job_alert_min_match_score: int = 0  # 0 = accept all, 1-99 = auto-reject below threshold
    # Promotion bounds: at most this many scheduled jobs start runs per poll cycle (0 = none) ...
    job_alert_max_promotions_per_cycle: int = 5
    # ... and alerts older than this aren't processed; SCHEDULED rows older than this expire.
    job_alert_max_age_days: int = 14

    # Artifacts (local filesystem)
    artifacts_dir: str = "./artifacts"

    # Prompts / known answers config
    prompts_path: str = "config/prompts.yaml"
    personal_path: str = "config/personal.yaml"

    # App
    app_env: str = "dev"
    log_level: str = "INFO"

    @field_validator("artifacts_dir", "prompts_path", "personal_path")
    @classmethod
    def _from_project_root(cls, value: str) -> str:
        return str(PROJECT_ROOT / Path(value).expanduser())


settings = Settings()


import asyncio as _asyncio
from datetime import date
from typing import get_args

_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _read_settings_sync() -> dict:
    try:
        with open(_SETTINGS_PATH) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _write_settings_sync(data: dict) -> None:
    with open(_SETTINGS_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


async def get_runtime_settings() -> dict:
    """Read runtime settings (async-safe, always fresh). The raw file: whatever decides how a run
    behaves reads get_effective_runtime_settings() instead."""
    return await _asyncio.to_thread(_read_settings_sync)


async def set_runtime_setting(key: str, value) -> dict:
    """Update a single runtime setting and return the full settings dict.
    API writes are checked with validate_runtime_setting() first."""
    def _update():
        settings_dict = _read_settings_sync()
        settings_dict[key] = value
        _write_settings_sync(settings_dict)
        return settings_dict
    return await _asyncio.to_thread(_update)


# ── Runtime-setting validation (contract §3, §8) ─────────────────────────────

DEFAULT_REVIEW_WINDOW_HOURS = 48.0
DEFAULT_BROWSER_HEADED_FALLBACK = True


def _submission_mode(value):
    from yahbah.run_state import SubmissionMode  # (run_state imports the DB session, which imports this module)

    if value not in get_args(SubmissionMode):
        raise ValueError(f"must be one of {list(get_args(SubmissionMode))}")
    return value


def _positive_hours(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError("must be a number of hours greater than 0")
    return float(value)


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _whole(minimum: int):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"must be a whole number >= {minimum}")
        return value
    return check


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value


def _choice(*options):
    def check(value):
        if value not in options:
            raise ValueError(f"must be one of {list(options)}")
        return value
    return check


def _sender_patterns(value):
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError("must be a list of sender patterns")
    return value


def _archive_by_status(value):
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
        raise ValueError("must map status types to true or false")
    return value


def _iso_date(value):
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("must be an ISO date (YYYY-MM-DD)") from None
    return value


# Every key PUT /settings/{key} may write, with its validator (which returns the value to store).
RUNTIME_SETTINGS = {
    "submission_mode": _submission_mode,
    "review_window_hours": _positive_hours,
    "browser_headed_fallback": _flag,
    "gmail_status_polling_enabled": _flag,
    "gmail_status_polling_interval_minutes": _whole(1),
    "gmail_folder_label": _text,
    "gmail_status_label": _text,
    "gmail_verification_code_action": _choice("archive", "delete", "nothing"),
    "gmail_auto_archive": _archive_by_status,
    "job_alert_polling_enabled": _flag,
    "job_alert_polling_interval_minutes": _whole(1),
    "job_alert_label": _text,
    "job_alert_auto_archive": _flag,
    "job_alert_sender_patterns": _sender_patterns,
    "job_alert_promotion_delay_hours": _whole(0),
    "job_alert_min_match_score": _whole(0),
    "job_alert_max_promotions_per_cycle": _whole(0),
    "job_alert_max_age_days": _whole(1),
    "job_alert_backfill_since": _iso_date,
}


def validate_runtime_setting(key: str, value):
    """The value to store for `key`; ValueError for an unknown key or an invalid value."""
    if key not in RUNTIME_SETTINGS:
        raise ValueError(f"unknown setting {key!r}")
    try:
        return RUNTIME_SETTINGS[key](value)
    except ValueError as exc:
        raise ValueError(f"{key} {exc}") from None


def effective_runtime_settings(raw: dict) -> dict:
    """`raw` with the settings new runs snapshot, as they use them: a missing or invalid submission_mode
    means review (fail closed); a missing or invalid window means 48 hours; a missing or invalid
    browser_headed_fallback means true (contract §2)."""
    from yahbah.run_state import DEFAULT_SUBMISSION_MODE

    out = dict(raw)
    for key, default in (("submission_mode", DEFAULT_SUBMISSION_MODE),
                         ("review_window_hours", DEFAULT_REVIEW_WINDOW_HOURS),
                         ("browser_headed_fallback", DEFAULT_BROWSER_HEADED_FALLBACK)):
        try:
            out[key] = validate_runtime_setting(key, raw[key])
        except (KeyError, ValueError):
            out[key] = default
    return out


async def get_effective_runtime_settings() -> dict:
    return effective_runtime_settings(await get_runtime_settings())


_prompts_cache: dict = {}


def load_prompts_config() -> dict:
    """prompts.yaml merged with personal.yaml (prompts such as cover_letter, known_answers, profile).

    Re-read whenever either file changes (mtime or size), so an edited stored answer applies to the
    next call without a worker restart. prompts.yaml is safe to publish; personal.yaml contains
    personal info and is gitignored.
    """
    paths = (Path(settings.prompts_path), Path(settings.personal_path))
    stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if _prompts_cache.get("stamp") != stamp:
        _prompts_cache.update(stamp=stamp, config=_read_prompts_config(*paths))
    return _prompts_cache["config"]


load_prompts_config.cache_clear = _prompts_cache.clear  # forces a re-read (tests)


def _read_prompts_config(prompts_path: Path, personal_path: Path) -> dict:
    with open(prompts_path) as f:
        config = yaml.safe_load(f)

    with open(personal_path) as f:
        personal = yaml.safe_load(f) or {}

    # Merge personal prompts (e.g. cover_letter) into the prompts dict
    if "prompts" in personal:
        config.setdefault("prompts", {}).update(personal["prompts"])

    # Merge known_answers
    if "known_answers" in personal:
        config["known_answers"] = personal["known_answers"]

    # Merge profile
    if "profile" in personal:
        config["profile"] = personal["profile"]

    return config
