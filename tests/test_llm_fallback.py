"""LLM fallback chain: switch on exhausted credit, keep real errors visible."""

import pytest

from outreach.http_util import HttpError
from outreach.llm import FallbackLlmClient, LlmUnavailable
from outreach.protocols import LlmMessage, LlmResponse

MSG = [LlmMessage(role="user", content="hi")]
QUOTA_BODY = '{"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}}'


class Fake:
    def __init__(self, provider: str, error: Exception | None = None):
        self.provider = provider
        self.error = error
        self.calls = 0

    def complete(self, messages, *, temperature=0.2, response_format_json=False):
        self.calls += 1
        if self.error:
            raise self.error
        return LlmResponse(content=f"from {self.provider}", model=self.provider)


def test_falls_back_when_credit_exhausted_and_skips_dead_provider() -> None:
    openai = Fake("openai", HttpError("429", status_code=429, body=QUOTA_BODY))
    gemini = Fake("gemini")
    client = FallbackLlmClient([openai, gemini])
    assert client.complete(MSG).content == "from gemini"
    assert client.complete(MSG).content == "from gemini"
    assert openai.calls == 1  # not retried after being marked exhausted
    assert client.provider == "gemini"


def test_overloaded_model_moves_on_and_cools_down() -> None:
    busy = Fake("gemini/flash", HttpError("503", status_code=503, body="high demand"))
    lite = Fake("gemini/lite")
    client = FallbackLlmClient([busy, lite])
    assert client.complete(MSG).content == "from gemini/lite"
    assert client.complete(MSG).content == "from gemini/lite"
    assert busy.calls == 1  # cooling down, not hammered on every call
    assert "gemini/flash" not in client.dead  # overload is temporary


def test_gemini_rate_limit_is_not_treated_as_exhausted() -> None:
    rpm = HttpError("429", status_code=429, body="You exceeded your current quota, retry in 20s")
    client = FallbackLlmClient([Fake("gemini/flash", rpm), Fake("gemini/lite")])
    client.complete(MSG)
    assert "gemini/flash" not in client.dead


def test_gemini_daily_cap_is_exhausted_despite_billing_wording() -> None:
    daily = HttpError(
        "429",
        status_code=429,
        body="You exceeded your current quota, please check your plan and billing details. "
        "limit: 20, model: gemini-flash\nPlease retry in 20h44m13.95s.",
    )
    flash = Fake("gemini/flash", daily)
    client = FallbackLlmClient([flash, Fake("gemini/lite")])
    client.complete(MSG)
    assert "gemini/flash" in client.dead


def test_groq_daily_limit_wording_is_exhausted_but_tpm_limit_is_not() -> None:
    rpd = HttpError("429", status_code=429, body="requests per day (RPD): Limit 1000. Please try again in 1h2m3.5s.")
    tpm = HttpError("429", status_code=429, body="tokens per minute (TPM). Please try again in 7.66s.")
    client = FallbackLlmClient([Fake("groq/a", rpd), Fake("groq/b", tpm), Fake("gemini")])
    client.complete(MSG)
    assert "groq/a" in client.dead
    assert "groq/b" not in client.dead


def test_every_provider_out_of_quota_raises_llm_unavailable_without_calls() -> None:
    quota = HttpError("429", status_code=429, body=QUOTA_BODY)
    openai = Fake("openai", quota)
    client = FallbackLlmClient([openai])
    with pytest.raises(LlmUnavailable):
        client.complete(MSG)
    with pytest.raises(LlmUnavailable):
        client.complete(MSG)
    assert openai.calls == 1


def test_bad_request_is_not_hidden() -> None:
    client = FallbackLlmClient([Fake("openai", HttpError("400", status_code=400, body="bad")), Fake("gemini")])
    with pytest.raises(HttpError):
        client.complete(MSG)


def test_all_providers_down_raises_clear_error() -> None:
    dead = HttpError("401", status_code=401, body="invalid key")
    client = FallbackLlmClient([Fake("openai", dead), Fake("gemini", dead)])
    with pytest.raises(RuntimeError, match="All LLM providers"):
        client.complete(MSG)
