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

    # Artifacts (local filesystem)
    artifacts_dir: str = "./artifacts"

    # App
    app_env: str = "dev"
    log_level: str = "INFO"


settings = Settings()
