"""A replaceable, local-first streaming inference adapter.

This module never starts a model, downloads weights, or silently selects a cloud
provider. Remote endpoints are rejected by default. Credentials are not stored.
"""
from __future__ import annotations

import json
from typing import Iterator, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """Inference failed without exposing private prompt content."""


class ChatProvider(Protocol):
    def stream(self, messages: Sequence[dict[str, str]]) -> Iterator[str]:
        """Produce text chunks for one complete assistant response."""


class LocalOpenAIProvider:
    """OpenAI-compatible /v1/chat/completions SSE client for local Colibri.

    An external HTTPS endpoint requires an explicit allow_remote=True and is
    not selected automatically. No API credentials or user data are persisted.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:8000/v1",
        timeout: float = 120.0,
        allow_remote: bool = False,
        api_key: str | None = None,
    ) -> None:
        if not model or not model.strip():
            raise ValueError("model must be an explicitly configured model ID")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be HTTP(S)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query or fragment")
        loopback = parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
        if not loopback and (not allow_remote or parsed.scheme != "https"):
            raise ValueError("remote providers require explicit consent and HTTPS")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.model = model
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.timeout = timeout
        self.api_key = api_key

    def stream(self, messages: Sequence[dict[str, str]]) -> Iterator[str]:
        body = json.dumps({
            "model": self.model,
            "messages": list(messages),
            "stream": True,
        }, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        finished = False
        try:
            with urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    if not data:
                        continue
                    try:
                        payload = json.loads(data)
                        if "error" in payload:
                            raise ProviderError("provider reported an inference error")
                        choice = payload.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            yield content
                        if choice.get("finish_reason") is not None:
                            finished = True
                    except (ValueError, IndexError, KeyError, TypeError) as exc:
                        raise ProviderError("invalid response event from inference provider") from exc
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ProviderError("inference endpoint unavailable or rejected the request") from exc
        if not finished:
            raise ProviderError("inference stream ended before completion")
