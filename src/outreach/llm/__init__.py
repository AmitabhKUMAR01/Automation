"""Thin LLM adapters (OpenAI-compatible / Anthropic) behind LlmClient protocol."""

from __future__ import annotations

import time
from typing import Any

from outreach.config import Settings, get_settings
from outreach.http_util import HttpError, request_json, request_json_once
from outreach.logging import get_logger
from outreach.protocols import LlmClient, LlmMessage, LlmResponse

log = get_logger("llm")

OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIClient:
    """Any OpenAI-compatible chat API (OpenAI, Gemini's /openai endpoint, ...)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: int = 60,
        base_url: str = OPENAI_BASE_URL,
        provider: str = "openai",
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")
        self.provider = provider
        # A fallback chain turns this off for all but its last client
        self.retry_transient = True

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

        send = request_json if self.retry_transient else request_json_once
        data = send(
            "POST",
            f"{self.base_url}/chat/completions",
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

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: int = 60,
        base_url: str = "https://api.anthropic.com",
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

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

        endpoint = (
            f"{self.base_url}/messages"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/messages"
        )
        headers = {
            "x-api-key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "claude-code-20250219",
            "user-agent": "claude-cli/0.2.29 (external, sdk-cli)",
            "Content-Type": "application/json",
        }
        data = request_json(
            "POST",
            endpoint,
            headers=headers,
            json=payload,
            timeout=float(self.timeout_seconds),
        )
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return LlmResponse(content=text, model=self.model, raw=data)


# Paid-credit exhaustion only. Gemini's per-minute limit also says "exceeded your current quota",
# and that one recovers, so it must stay transient.
_QUOTA_HINTS = ("insufficient_quota", "credit_balance", "billing")


def _is_exhausted(exc: HttpError) -> bool:
    """Provider/model is unusable for the rest of the run (no credit, bad key, retired model)."""
    if exc.status_code in {401, 402, 403, 404}:
        return True
    body = (exc.body or "").lower()
    return exc.status_code == 429 and any(h in body for h in _QUOTA_HINTS)


def _is_transient(exc: HttpError) -> bool:
    return exc.status_code is not None and (exc.status_code == 429 or exc.status_code >= 500)


class FallbackLlmClient:
    """Try providers in order; skip a provider for the rest of the run once it is out of credit."""

    TRANSIENT_COOLDOWN_SECONDS = 120

    def __init__(self, clients: list[LlmClient]):
        if not clients:
            raise RuntimeError("No LLM providers configured (check API keys in .env)")
        self.clients = clients
        self.dead: set[str] = set()
        self.cooling_until: dict[str, float] = {}
        self.provider = clients[0].provider
        # Fail fast on overload while another model can take the call; the last one keeps retrying
        for c in clients[:-1]:
            if hasattr(c, "retry_transient"):
                c.retry_transient = False

    def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.2,
        response_format_json: bool = False,
    ) -> LlmResponse:
        last_exc: Exception | None = None
        now = time.monotonic()
        alive = [c for c in self.clients if c.provider not in self.dead]
        ready = [c for c in alive if self.cooling_until.get(c.provider, 0) <= now]
        # If everything is cooling down, still try rather than fail without a request
        for client in ready or alive:
            try:
                resp = client.complete(
                    messages, temperature=temperature, response_format_json=response_format_json
                )
                self.provider = client.provider
                return resp
            except HttpError as exc:
                last_exc = exc
                if _is_exhausted(exc):
                    self.dead.add(client.provider)
                    log.warning(
                        "llm_provider_exhausted",
                        provider=client.provider,
                        status=exc.status_code,
                        next=self._next_alive_name(),
                    )
                    continue
                if _is_transient(exc):
                    self.cooling_until[client.provider] = time.monotonic() + self.TRANSIENT_COOLDOWN_SECONDS
                    log.warning("llm_provider_failed_trying_next", provider=client.provider, status=exc.status_code)
                    continue
                raise
        raise RuntimeError(
            f"All LLM providers failed ({', '.join(c.provider for c in self.clients)}): {last_exc}"
        ) from last_exc

    def _next_alive_name(self) -> str | None:
        return next((c.provider for c in self.clients if c.provider not in self.dead), None)


def _build(name: str, settings: Settings, *, all_gemini_models: bool) -> list[LlmClient]:
    s = settings.secrets
    timeout = settings.config.llm.timeout_seconds
    if name == "openai" and s.openai_api_key:
        return [OpenAIClient(s.openai_api_key, s.openai_model, timeout_seconds=timeout)]
    if name == "gemini" and s.gemini_api_key:
        models = [m.strip() for m in s.gemini_models.split(",") if m.strip()] if all_gemini_models else []
        models = models or [s.gemini_model]
        return [
            OpenAIClient(
                s.gemini_api_key,
                model,
                timeout_seconds=timeout,
                base_url=s.gemini_base_url,
                provider=f"gemini/{model}",
            )
            for model in models
        ]
    if name == "anthropic" and s.anthropic_api_key:
        return [
            AnthropicClient(
                s.anthropic_api_key,
                s.anthropic_model,
                timeout_seconds=timeout,
                base_url=s.anthropic_base_url,
            )
        ]
    return []


def get_llm_client(settings: Settings | None = None) -> LlmClient:
    settings = settings or get_settings()
    chain = settings.secrets.llm_providers
    if chain:
        names = [n.strip().lower() for n in chain.split(",") if n.strip()]
        clients: list[LlmClient] = []
        for n in names:
            built = _build(n, settings, all_gemini_models=True)
            if not built:
                log.warning("llm_provider_missing_key", provider=n)
            clients.extend(built)
        return FallbackLlmClient(clients)

    provider = settings.secrets.llm_provider.lower().strip()
    built = _build(provider, settings, all_gemini_models=False)
    if not built:
        raise RuntimeError(
            f"LLM_PROVIDER={provider} needs its API key in .env "
            "(OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY)"
        )
    return built[0]
