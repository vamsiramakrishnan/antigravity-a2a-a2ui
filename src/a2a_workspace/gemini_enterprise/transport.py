"""Pluggable HTTP transport for the Discovery Engine client.

Kept behind a tiny protocol for two reasons: it lets the client run on the
standard library (no new runtime dependency), and it makes every Discovery
Engine call trivially mockable in tests without network access.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class TransportError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body.decode() or "null")


@runtime_checkable
class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Default transport using the standard library."""

    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        self._timeout = timeout_seconds

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return HttpResponse(status=resp.status, body=resp.read())
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise TransportError(exc.code, exc.read().decode(errors="replace")) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise TransportError(0, str(exc.reason)) from exc
