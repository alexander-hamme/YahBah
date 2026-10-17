"""
Ollama async client.

Wraps the Ollama /api/chat endpoint with:
- a role (driver / prose / vision / escalation) that picks the model and its
  fixed request options from yahbah.llm.models, so models never reload
- structured output constrained by the Pydantic model's JSON schema
- optional images (base64) for the vision role
- every raw reply logged to the current run's llm/raw.jsonl
"""
import base64
import re
import time
from typing import Type, TypeVar

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError

from yahbah.config import settings
from yahbah.llm import logs
from yahbah.llm.models import Role, spec, ollama_options

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")

# Prose clean-up only: never applied to structured output, where it could alter labels/values.
_PROSE_REPLACEMENTS = {"*": "", "—": "-"}


class LLMError(Exception):
    pass


class OllamaClient:
    def __init__(self, role: Role = "driver") -> None:
        self._role = role
        self._base_url = settings.ollama_base_url
        self._timeout = settings.ollama_timeout

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        images: list[bytes] | None = None,
    ) -> T:
        """Chat with the reply constrained to response_model's JSON schema. Raises LLMError on any failure."""
        raw = await self._call_ollama(
            system_prompt, user_prompt, schema=response_model.model_json_schema(), images=images
        )
        raw = _CODE_FENCE.sub("", raw)  # vision models sometimes wrap JSON in fences anyway
        try:
            return response_model.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMError(f"LLM output failed schema validation: {exc}\nRaw: {raw[:500]}") from exc

    async def generate_text(
        self, system_prompt: str, user_prompt: str, images: list[bytes] | None = None
    ) -> str:
        """Plain text generation — no JSON format enforcement."""
        text = await self._call_ollama(system_prompt, user_prompt, images=images)
        for symb, repl in _PROSE_REPLACEMENTS.items():
            text = text.replace(symb, repl)
        return text

    async def _call_ollama(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        images: list[bytes] | None = None,
    ) -> str:
        s = spec(self._role)
        options = ollama_options(self._role)
        user_msg: dict = {"role": "user", "content": user_prompt}
        if images:
            user_msg["images"] = [base64.b64encode(img).decode() for img in images]
        payload: dict = {
            "model": s.model,
            "messages": [{"role": "system", "content": system_prompt}, user_msg],
            "stream": False,
            "think": options.pop("think"),
            "keep_alive": options.pop("keep_alive"),
            "options": {**options, "temperature": 0.1 if schema else 0.7},
        }
        if schema:
            payload["format"] = schema

        logger.debug(f"Ollama request role={self._role} model={s.model}")
        started = time.time()
        timeout = httpx.Timeout(connect=10.0, read=self._timeout, write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(f"{self._base_url}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                logs.append("raw.jsonl", {"role": self._role, "model": s.model, "error": "timeout"})
                raise LLMError(f"Ollama request timed out after {self._timeout}s") from exc
            except httpx.HTTPStatusError as exc:
                logs.append("raw.jsonl", {"role": self._role, "model": s.model, "error": exc.response.text[:300]})
                raise LLMError(
                    f"Ollama HTTP error {exc.response.status_code}: {exc.response.text[:300]}"
                ) from exc
            body = resp.json()

        message = body.get("message") or {}
        logs.append("raw.jsonl", {
            "role": self._role,
            "model": s.model,
            "think": s.think,
            "secs": round(time.time() - started, 1),
            "prompt_tokens": body.get("prompt_eval_count"),
            "output_tokens": body.get("eval_count"),
            "done_reason": body.get("done_reason"),
            "images": len(images or []),
            "thinking": message.get("thinking"),
            "content": message.get("content"),
        })
        if "content" not in message:
            raise LLMError(f"Unexpected Ollama response shape: {str(body)[:300]}")
        if body.get("done_reason") == "length":
            raise LLMError(f"Ollama output hit num_predict={s.num_predict} (runaway generation)")
        return message["content"]
