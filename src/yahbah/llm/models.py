"""
Model registry: which Ollama model plays which role, and the exact request
options for each. Every LLM client in YahBah (our OllamaClient and browser-use's
ChatOllama) takes its options from here, so a model is always requested with
the same num_ctx and never reloads mid-run.

Roles:
  driver      qwen3-coder-next — agent loops, DOM/HTML work, mapping JSON, page judging (text only)
  prose       qwen3.8:27b      — cover letters, free-text answers
  vision      qwen3.8:27b      — look_at_screen, reading a page from a screenshot
  escalation  qwen3.8:27b      — thinking + screenshots for a few steps when the driver stalls
"""
from dataclasses import dataclass
from typing import Literal

from yahbah.config import settings

Role = Literal["driver", "prose", "vision", "escalation"]


@dataclass(frozen=True)
class ModelSpec:
    model: str
    num_ctx: int
    think: bool = False

    @property
    def num_predict(self) -> int:
        # Always capped: an uncapped model listing 244 countries generates for 10+ minutes.
        return 6144 if self.think else 4096


def spec(role: Role) -> ModelSpec:
    if role == "driver":
        return ModelSpec(settings.model_driver, settings.ctx_driver)
    return ModelSpec(
        settings.model_prose_vision,
        settings.ctx_prose_vision,
        think=role == "escalation",
    )


def ollama_options(role: Role) -> dict:
    """Options for a ChatOllama / raw /api/chat request in this role."""
    s = spec(role)
    return {
        "num_ctx": s.num_ctx,
        "num_predict": s.num_predict,
        "think": s.think,
        "keep_alive": settings.ollama_keep_alive,
    }
