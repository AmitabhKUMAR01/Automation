"""HTTP helpers: timeouts + exponential backoff retries."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from outreach.logging import get_logger

log = get_logger("http")

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class HttpError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _should_retry_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, HttpError) and exc.status_code is not None:
        return _should_retry_status(exc.status_code)
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
    timeout: httpx.Timeout | float | None = None,
) -> Any:
    """Perform an HTTP request and return parsed JSON with retries."""
    t = timeout if timeout is not None else DEFAULT_TIMEOUT
    with httpx.Client(timeout=t) as client:
        response = client.request(method, url, headers=headers, params=params, json=json)
        if _should_retry_status(response.status_code):
            log.warning(
                "http_retryable",
                status=response.status_code,
                url=url,
                body=response.text[:300],
            )
            raise HttpError(
                f"retryable status {response.status_code}",
                status_code=response.status_code,
                body=response.text[:500],
            )
        if response.status_code >= 400:
            raise HttpError(
                f"HTTP {response.status_code} for {url}",
                status_code=response.status_code,
                body=response.text[:1000],
            )
        if not response.content:
            return None
        return response.json()
