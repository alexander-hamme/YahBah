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

    # Artifacts (local filesystem)
    artifacts_dir: str = "./artifacts"

    # Prompts / known answers config
    prompts_path: str = "config/prompts.yaml"

    # App
    app_env: str = "dev"
    log_level: str = "INFO"


settings = Settings()


@lru_cache
def load_prompts_config() -> dict:
    """Load prompts and known answers from YAML. Cached per process."""
    with open(Path(settings.prompts_path)) as f:
        return yaml.safe_load(f)
