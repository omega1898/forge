"""Model adapters: a scripted fake, and an OpenAI-compatible HTTP client.

The adapter interface is deliberately small (blueprint Part 5.2): probe,
count_tokens, generate, cancel. Adapters normalize transport; they never
validate meaning. Everything they return is untrusted.

FakeModelAdapter replays fixtures (valid proposal, malformed JSON,
unsupported action, prose instead of JSON, truncated stream, slow, hang)
so storage, scheduling and validation can be tested with no model at all.

OpenAICompatibleAdapter talks to a local inference server such as
llama.cpp's llama-server through its chat-completions API. Only loopback
endpoints are allowed here; remote inference hosts need the authenticated
host channel, not plain HTTP.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections import deque
from typing import Any, Protocol
from urllib.parse import urlsplit

from forge.contracts.errors import ErrorCode, ForgeError
from forge.gateway.contracts import GenerationRequest, ModelConfig, RawGeneration, TokenCount

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ModelAdapter(Protocol):
    def count_tokens(self, config: ModelConfig, request: GenerationRequest) -> TokenCount: ...

    def generate(self, config: ModelConfig, request: GenerationRequest) -> RawGeneration: ...

    def cancel(self, call_id: str) -> bool: ...


class AdapterTimeout(Exception):
    pass


class AdapterTransportError(Exception):
    pass


# ------------------------------------------------------------------- fake --


class FakeModelAdapter:
    """Returns scripted responses in order. Tokens = whitespace-separated words."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses: deque[Any] = deque(responses or [])
        self.requests: list[GenerationRequest] = []
        self.cancelled: set[str] = set()
        self.release = threading.Event()
        self.confirm_cancel = True

    def script(self, *responses: Any) -> None:
        self.responses.extend(responses)

    def count_tokens(self, config: ModelConfig, request: GenerationRequest) -> TokenCount:
        words = sum(len(message.content.split()) + 4 for message in request.messages)
        return TokenCount(count=words, mode="exact_template", tokenizer_id="fake-whitespace")

    def generate(self, config: ModelConfig, request: GenerationRequest) -> RawGeneration:
        self.requests.append(request)
        response = self.responses.popleft() if self.responses else {"kind": "text", "text": "{}"}
        if isinstance(response, dict) and response.get("kind") == "hang":
            # Keeps "computing" until released or cancelled.
            while not self.release.wait(0.01):
                if request.call_id in self.cancelled and self.confirm_cancel:
                    raise AdapterTimeout("cancelled")
            return RawGeneration(text=json.dumps(response.get("then", {})), finished=True)
        if isinstance(response, dict) and response.get("kind") == "slow":
            time.sleep(response["seconds"])
            return RawGeneration(text=json.dumps(response.get("then", {})), finished=True)
        if isinstance(response, dict) and response.get("kind") == "transport_error":
            raise AdapterTransportError("connection reset by peer")
        if isinstance(response, dict) and response.get("kind") == "truncated":
            return RawGeneration(text=response["text"], finished=False, finish_reason="stream_ended")
        if isinstance(response, dict) and response.get("kind") == "text":
            return RawGeneration(text=response["text"], finished=True, finish_reason="stop",
                                 completion_tokens=len(response["text"].split()))
        text = json.dumps(response)
        return RawGeneration(text=text, finished=True, finish_reason="stop", completion_tokens=len(text.split()))

    def cancel(self, call_id: str) -> bool:
        self.cancelled.add(call_id)
        return self.confirm_cancel


# ------------------------------------------------------ OpenAI-compatible --


class OpenAICompatibleAdapter:
    """Chat-completions transport for a local server (for example llama-server)."""

    def __init__(self) -> None:
        self._active: dict[str, http.client.HTTPConnection] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _endpoint(config: ModelConfig) -> tuple[str, int]:
        parts = urlsplit(config.endpoint or "")
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1") or parts.port is None:
            raise ForgeError(ErrorCode.PERMISSION_DENIED,
                             "model endpoints must be loopback http with an explicit port")
        return parts.hostname, parts.port

    def _post(self, config: ModelConfig, path: str, body: dict[str, Any], timeout: float, call_id: str | None = None) -> dict:
        host, port = self._endpoint(config)
        # http.client directly: no proxy inheritance, no redirects followed.
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        if call_id:
            with self._lock:
                self._active[call_id] = connection
        try:
            connection.request("POST", path, body=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
        except TimeoutError as error:
            raise AdapterTimeout(str(error)) from None
        except OSError as error:
            raise AdapterTransportError(f"{type(error).__name__}: {error}") from None
        finally:
            connection.close()
            if call_id:
                with self._lock:
                    self._active.pop(call_id, None)
        if len(data) > MAX_RESPONSE_BYTES:
            raise AdapterTransportError("response exceeds the size limit")
        if response.status != 200:
            raise AdapterTransportError(f"HTTP {response.status}")
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            raise AdapterTransportError("server response is not JSON") from None

    def count_tokens(self, config: ModelConfig, request: GenerationRequest) -> TokenCount:
        text = "\n".join(message.content for message in request.messages)
        try:
            data = self._post(config, "/tokenize", {"content": text}, timeout=30)
            tokens = data.get("tokens")
            if isinstance(tokens, list):
                # Message bodies only: the chat template adds tokens we did not count.
                return TokenCount(count=len(tokens), mode="text_only", tokenizer_id=config.model_name)
        except (AdapterTransportError, AdapterTimeout):
            pass
        return TokenCount(count=len(text) // 3 + 1, mode="estimated", tokenizer_id="chars_div_3")

    def generate(self, config: ModelConfig, request: GenerationRequest) -> RawGeneration:
        body: dict[str, Any] = {
            "model": config.model_name,
            "messages": [message.model_dump() for message in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": config.temperature,
            "stream": False,
        }
        if request.output_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": request.slot_id, "schema": request.output_schema}}
        data = self._post(config, "/v1/chat/completions", body, timeout=request.deadline_ms / 1000,
                          call_id=request.call_id)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            raise AdapterTransportError("response has no completion choice") from None
        usage = data.get("usage") or {}
        return RawGeneration(
            text=text,
            finished=finish in ("stop", None),
            finish_reason=finish,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    def cancel(self, call_id: str) -> bool:
        """Closing the connection does not prove the server stopped computing."""
        with self._lock:
            connection = self._active.get(call_id)
        if connection is not None:
            try:
                connection.sock and connection.sock.close()
            except OSError:
                pass
        return False  # cancel_unconfirmed: keep the reservation until resolved
