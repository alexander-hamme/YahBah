"""
Ollama async client.

Wraps the Ollama /api/chat endpoint with:
- structured JSON output (format="json")
- Pydantic schema validation
- configurable timeout
- clear error on validation failure

Designed to swap to Anthropic/OpenAI by replacing _call_ollama with
a different backend while keeping the same public interface.
"""
import json
from typing import Any, TypeVar, Type

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError

from yahbah.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class OllamaClient:
    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> T:
        """
        Sends a chat request to Ollama with format="json" and parses the
        response into response_model. Raises LLMError on any failure.
        """
        raw = await self._call_ollama(system_prompt, user_prompt)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned invalid JSON: {exc}\nRaw: {raw[:500]}") from exc

        try:
            return response_model.model_validate(data)
        except ValidationError as exc:
            raise LLMError(
                f"LLM output failed schema validation: {exc}\nData: {data}"
            ) from exc

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        """Plain text generation — no JSON format enforcement."""
        return await self._call_ollama(system_prompt, user_prompt, json_format=False)

    async def _call_ollama(
        self, system_prompt: str, user_prompt: str, json_format: bool = True
    ) -> str:
        payload: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.1 if json_format else 0.7},
        }
        if json_format:
            payload["format"] = "json"

        logger.debug(f"Ollama request to model {self._model}")

        timeout = httpx.Timeout(connect=10.0, read=self._timeout, write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise LLMError(f"Ollama request timed out after {self._timeout}s") from exc
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"Ollama HTTP error {exc.response.status_code}: {exc.response.text[:300]}"
                ) from exc

            body = resp.json()

        try:
            return body["message"]["content"] #  TODO .replace('*', '')
        except KeyError as exc:
            raise LLMError(f"Unexpected Ollama response shape: {body}") from exc
