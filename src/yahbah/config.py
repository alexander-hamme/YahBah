from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://localhost/yahbah"

    # Temporal
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "job-applications"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3:70b"
    ollama_timeout: int = 300  # seconds — read timeout for LLM generation

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

    # Artifacts (local filesystem)
    artifacts_dir: str = "./artifacts"

    # Prompts / known answers config
    prompts_path: str = "config/prompts.yaml"
    personal_path: str = "config/personal.yaml"

    # App
    app_env: str = "dev"
    log_level: str = "INFO"


settings = Settings()


import asyncio as _asyncio

_SETTINGS_PATH = Path("config/settings.yaml")


def _read_settings_sync() -> dict:
    try:
        with open(_SETTINGS_PATH) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {"testing_mode": False}


def _write_settings_sync(data: dict) -> None:
    with open(_SETTINGS_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


async def get_runtime_settings() -> dict:
    """Read runtime settings (async-safe, always fresh)."""
    return await _asyncio.to_thread(_read_settings_sync)


async def set_runtime_setting(key: str, value) -> dict:
    """Update a single runtime setting and return the full settings dict."""
    def _update():
        settings_dict = _read_settings_sync()
        settings_dict[key] = value
        _write_settings_sync(settings_dict)
        return settings_dict
    return await _asyncio.to_thread(_update)


@lru_cache
def load_prompts_config() -> dict:
    """Load prompts from prompts.yaml, merge with personal.yaml (cover_letter + known_answers).

    prompts.yaml is safe to publish; personal.yaml contains personal info and is gitignored.
    """
    with open(Path(settings.prompts_path)) as f:
        config = yaml.safe_load(f)

    with open(Path(settings.personal_path)) as f:
        personal = yaml.safe_load(f)

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
