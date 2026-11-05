"""Client for the local API, used by the CLI (and by tests).

Uses http.client directly rather than urllib: urllib honors proxy
environment variables, which could route a loopback request (and its
token) through a proxy.
"""

from __future__ import annotations

import http.client
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from forge.contracts.ids import new_id


class RpcError(Exception):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "error"))
        self.code = error.get("code")
        self.message = error.get("message", "")
        self.data = error.get("data", {}) or {}

    @property
    def forge_code(self) -> str | None:
        return self.data.get("code")


class ForgeClient:
    def __init__(self, url: str, token: str, *, timeout: float = 60.0) -> None:
        parts = urlsplit(url)
        if parts.hostname not in ("127.0.0.1", "localhost") or parts.scheme != "http":
            raise ValueError("the local client only connects to loopback http")
        self.host, self.port = parts.hostname, parts.port
        self.token = token
        self.timeout = timeout

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def call(self, method: str, params: dict[str, Any] | None = None, *, request_id: str | None = None) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": request_id or new_id("request"), "method": method,
                           "params": params or {}}).encode()
        connection = self._connection()
        try:
            connection.request("POST", "/rpc", body=body, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                "Host": f"{self.host}:{self.port}",
            })
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        if response.status != 200:
            raise RpcError({"code": response.status, "message": payload.decode("utf-8", "replace")[:500]})
        message = json.loads(payload)
        if "error" in message:
            raise RpcError(message["error"])
        return message["result"]

    def mutate(self, method: str, project_id: str, params: dict[str, Any], *, key: str | None = None,
               expected_record_version: int | None = None) -> Any:
        envelope: dict[str, Any] = {"project_id": project_id, "idempotency_key": key or new_id("request"),
                                    "params": params}
        if expected_record_version is not None:
            envelope["expected_record_version"] = expected_record_version
        return self.call(method, envelope)

    def events(self, *, after: int = 0) -> Iterator[dict[str, Any]]:
        """Yield events as they arrive (backfill first). Detects sequence gaps."""
        connection = http.client.HTTPConnection(self.host, self.port, timeout=None)
        connection.request("GET", f"/events?after={int(after)}", headers={
            "Authorization": f"Bearer {self.token}", "Host": f"{self.host}:{self.port}",
            "Accept": "text/event-stream",
        })
        response = connection.getresponse()
        if response.status != 200:
            raise RpcError({"code": response.status, "message": response.read().decode()[:500]})
        expected = after + 1
        fields: dict[str, str] = {}
        try:
            while line := response.readline():
                text = line.decode("utf-8").rstrip("\n")
                if text.startswith(":"):
                    continue
                if text:
                    key, _, value = text.partition(": ")
                    fields[key] = value
                    continue
                if fields.get("event") == "reload":
                    yield {"reload": True}
                    return
                if "data" in fields:
                    event = json.loads(fields["data"])
                    if event["event_seq"] != expected:
                        # A gap means missed events: recover by re-reading, never guess.
                        yield {"gap": True, "expected": expected, "received": event["event_seq"]}
                        return
                    expected += 1
                    yield event
                fields = {}
        finally:
            connection.close()
