"""Thin LLM adapters (OpenAI / Anthropic) behind LlmClient protocol."""

from __future__ import annotations

from typing import Any

from outreach.config import Settings, get_settings
from outreach.http_util import request_json
from outreach.logging import get_logger
from outreach.protocols import LlmClient, LlmMessage, LlmResponse

log = get_logger("llm")


class OpenAIClient:
    provider = "openai"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: int = 60):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.2,
        response_format_json: bool = False,
    ) -> LlmResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        data = request_json(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=float(self.timeout_seconds),
        )
        content = data["choices"][0]["message"]["content"]
        return LlmResponse(content=content, model=self.model, raw=data)


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: int = 60):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.2,
        response_format_json: bool = False,
    ) -> LlmResponse:
        system = "\n".join(m.content for m in messages if m.role == "system")
        user_messages = [m for m in messages if m.role != "system"]
        if response_format_json and system:
            system = system + "\nRespond with valid JSON only."

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in user_messages],
        }
        if system:
            payload["system"] = system

        data = request_json(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=float(self.timeout_seconds),
        )
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return LlmResponse(content=text, model=self.model, raw=data)


def get_llm_client(settings: Settings | None = None) -> LlmClient:
    settings = settings or get_settings()
    provider = settings.secrets.llm_provider.lower().strip()
    timeout = settings.config.llm.timeout_seconds

    if provider == "openai":
        if not settings.secrets.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return OpenAIClient(
            settings.secrets.openai_api_key,
            settings.secrets.openai_model,
            timeout_seconds=timeout,
        )
    if provider == "anthropic":
        if not settings.secrets.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        return AnthropicClient(
            settings.secrets.anthropic_api_key,
            settings.secrets.anthropic_model,
            timeout_seconds=timeout,
        )
    raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")
