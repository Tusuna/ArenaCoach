"""Standalone Echo VR HTTP API client.

This intentionally does not use Observer modules or the echovr_api package.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from arena_coach.models import ConnectionStatus


class EchoApiError(RuntimeError):
    """Raised when the Echo API cannot return a usable JSON snapshot."""


class EchoApiClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6721,
        timeout: float = 1.0,
        path: str = "/session",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.path = self._normalize_path(path)

    @property
    def source_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = str(path or "/").strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized

    def fetch_snapshot(self) -> Dict[str, Any]:
        request = Request(self.source_url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            raise EchoApiError(f"Echo API returned HTTP {exc.code} from {self.source_url}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise EchoApiError(f"Echo API unavailable at {self.source_url}: {exc}") from exc

        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EchoApiError("Echo API response was not valid UTF-8") from exc

        try:
            snapshot = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise EchoApiError("Echo API response was not valid JSON") from exc

        if not isinstance(snapshot, dict):
            raise EchoApiError("Echo API response JSON was not an object")

        return snapshot

    def test_connection(self) -> ConnectionStatus:
        started = time.perf_counter()
        try:
            snapshot = self.fetch_snapshot()
        except EchoApiError as exc:
            return ConnectionStatus(
                ok=False,
                source=self.source_url,
                status="unavailable",
                error=str(exc),
            )

        latency_ms = (time.perf_counter() - started) * 1000
        return ConnectionStatus(
            ok=True,
            source=self.source_url,
            status="available",
            latency_ms=round(latency_ms, 2),
            snapshot_keys=sorted(str(key) for key in snapshot.keys()),
        )
