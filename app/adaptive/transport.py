"""Strict JSON and provider URL boundary for adaptive LLM calls."""

from __future__ import annotations

import ipaddress
import json
import math
import socket
from collections.abc import Mapping
from typing import ClassVar
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit, urlunsplit


class ProviderUrlError(ValueError):
    pass


class LLMTransportError(RuntimeError):
    pass


def parse_json_object(text: str) -> dict[str, object]:
    if not isinstance(text, str) or "```" in text:
        raise ValueError("response must be plain JSON")
    source = text.strip()
    try:
        value, end = json.JSONDecoder().raw_decode(source)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON object") from exc
    if end != len(source) or not isinstance(value, dict):
        raise ValueError("response must contain exactly one JSON object")
    return value


def _public_address(address: str) -> bool:
    value = ipaddress.ip_address(address)
    return not (
        value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_reserved
        or value.is_unspecified
    )


def validate_provider_url(value: str, *, allow_local_http: bool = False) -> str:
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ProviderUrlError("provider URL is invalid")
    addresses = {
        row[4][0]
        for row in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    }
    local = addresses and all(ipaddress.ip_address(address).is_loopback for address in addresses)
    if parsed.scheme != "https" and not (allow_local_http and local):
        raise ProviderUrlError("provider HTTPS is required")
    if not local and any(not _public_address(address) for address in addresses):
        raise ProviderUrlError("provider resolves to a disallowed address")
    path = parsed.path.rstrip("/") or "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StrictOpenAICompatibleTransport:
    """Bounded Chat Completions client that denies redirects and unsafe endpoints."""

    _VERBOSITY: ClassVar[frozenset[str]] = frozenset({"low", "medium", "high"})
    _REASONING: ClassVar[frozenset[str]] = frozenset(
        {"none", "minimal", "low", "medium", "high", "xhigh"}
    )

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model_verbosity: str | None = None,
        model_reasoning_effort: str | None = None,
        allow_local_http: bool = False,
    ) -> None:
        self.base_url = validate_provider_url(base_url, allow_local_http=allow_local_http)
        self.api_key = str(api_key)
        self.model_verbosity = self._control(model_verbosity, self._VERBOSITY, "verbosity")
        self.model_reasoning_effort = self._control(
            model_reasoning_effort,
            self._REASONING,
            "reasoning effort",
        )
        self._opener = urlrequest.build_opener(_NoRedirect)

    @staticmethod
    def _control(value: str | None, allowed: frozenset[str], name: str) -> str | None:
        if value is None or not str(value).strip():
            return None
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            raise ValueError(f"invalid model {name}")
        return normalized

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout: float | None = None,
    ) -> str:
        limit = 30.0 if timeout is None else float(timeout)
        if not math.isfinite(limit) or limit <= 0:
            raise LLMTransportError("invalid timeout")
        # Re-resolve immediately before every request to reduce DNS-rebinding exposure.
        validate_provider_url(self.base_url, allow_local_http=self.base_url.startswith("http://"))
        payload: dict[str, object] = {
            "model": str(model),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.model_verbosity:
            payload["verbosity"] = self.model_verbosity
        if self.model_reasoning_effort:
            payload["reasoning_effort"] = self.model_reasoning_effort
        if not self.model_verbosity and not self.model_reasoning_effort:
            payload["temperature"] = 0
        request = urlrequest.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=limit) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ValueError("response limit exceeded")
            decoded = json.loads(body.decode("utf-8"))
            choices = decoded.get("choices") if isinstance(decoded, Mapping) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise ValueError("malformed choices")
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            if not isinstance(content, str):
                raise TypeError("missing response text")
            return content
        except (
            OSError,
            ValueError,
            TypeError,
            UnicodeError,
            json.JSONDecodeError,
            urlerror.URLError,
            urlerror.HTTPError,
        ) as exc:
            raise LLMTransportError("LLM request failed") from exc
