"""Authenticated local API: JSON-RPC 2.0 over loopback HTTP, plus SSE events.

    POST /rpc     one JSON-RPC request per HTTP request (no batches)
    GET  /events  server-sent events: backfill after a cursor, then live

Binding to 127.0.0.1 is NOT authentication: any web page the user opens can
send requests to localhost. So every request must also pass, in order:

1. path and method are known;
2. Host header is exactly this server's loopback address (blocks DNS
   rebinding, where an attacker's domain resolves to 127.0.0.1);
3. no browser Origin header (no CORS is ever granted);
4. no query string carrying credentials (URLs leak into logs);
5. a per-client bearer token, compared in constant time;
6. bounded body size and JSON content type.

Mutations must carry a JSON-RPC id (an unanswered notification cannot carry
an approval) and an idempotency key. Errors carry stable FORGE codes and a
correlation ID, never stack traces or environment values.
"""

from __future__ import annotations

import base64
import hmac
import json
import queue
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from forge.contracts.commands import ActorContext
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.versions import SCHEMA_VERSION
from forge.record.store import MAX_PAGE

MAX_BODY_BYTES = 1024 * 1024
MAX_ARTIFACT_CHUNK = 1024 * 1024
PROTOCOL_VERSION = "1.0"
SSE_QUEUE_LIMIT = 1000
SSE_HEARTBEAT_SECONDS = 15.0

# API methods that map one-to-one onto controller commands. System-only
# commands (job.*, assessment.record, ...) are deliberately not exposed.
MUTATIONS = (
    "project.create", "intent.submit", "task.create", "task.cancel", "task.complete", "task.add_dependency",
    "plan.apply", "criterion.create", "requirement.create", "decision.request", "decision.record",
    "workspace.capture", "evidence.record_review", "suite.admit",
)
JSONRPC_PARSE_ERROR, JSONRPC_INVALID, JSONRPC_NO_METHOD, JSONRPC_PARAMS = -32700, -32600, -32601, -32602
JSONRPC_INTERNAL, JSONRPC_FORGE = -32603, -32000


class ApiServer:
    def __init__(
        self,
        controller,  # noqa: ANN001 - forge.controller.service.Controller
        *,
        tokens: dict[str, ActorContext],
        port: int = 0,
        job_service=None,  # noqa: ANN001
    ) -> None:
        self.controller = controller
        self.job_service = job_service
        self._tokens = dict(tokens)
        self.lock = threading.RLock()
        self.session_id = new_id("request")
        self._stopping = threading.Event()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.api = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self._thread: threading.Thread | None = None
        self._read_methods: dict[str, Callable[[ActorContext, dict[str, Any]], Any]] = {
            "session.hello": self._hello,
            "project.open": self._project_open,
            "project.resume": self._project_resume,
            "project.summary": self._project_summary,
            "task.list": self._task_list,
            "task.get": self._task_get,
            "evidence.list": self._evidence_list,
            "artifact.read": self._artifact_read,
            "events.list": self._events_list,
            "health.get": self._health,
            "authority.inspect": self._authority_inspect,
            "authority.approve_source_root": self._approve_source_root,
            # Compliance (forge.compliance.service). Setting the domain, attesting and importing are
            # refused there unless the caller is the trusted person.
            "compliance.domains": lambda actor, params: self._compliance().domains(),
            "compliance.set_domain": self._compliance_set_domain,
            "compliance.check": self._compliance_check,
            "compliance.report": self._compliance_report,
            "compliance.attest": self._compliance_attest,
            "compliance.import_catalog": self._compliance_import_catalog,
            "compliance.import_vulnerabilities": self._compliance_import_vulnerabilities,
        }

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def actor_for(self, token: str) -> ActorContext | None:
        found = None
        for known, actor in self._tokens.items():
            if hmac.compare_digest(known.encode(), token.encode()):
                found = actor
        return found

    def revoke_token(self, token: str) -> None:
        self._tokens.pop(token, None)

    # ------------------------------------------------------------- dispatch

    def dispatch(self, actor: ActorContext, method: str, params: dict[str, Any]) -> Any:
        with self.lock:
            if method in self._read_methods:
                return self._read_methods[method](actor, params)
            if method in MUTATIONS:
                envelope = {
                    "schema_version": SCHEMA_VERSION,
                    "method": method,
                    "idempotency_key": params.get("idempotency_key"),
                    "params": params.get("params", {}),
                }
                if method != "project.create":
                    envelope["project_id"] = params.get("project_id")
                if "expected_record_version" in params:
                    envelope["expected_record_version"] = params["expected_record_version"]
                receipt = self.controller.apply_command(envelope, actor)
                if self.job_service is not None and method == "task.cancel":
                    self.job_service.pump()
                return receipt.model_dump(mode="json")
        raise KeyError(method)

    # --------------------------------------------------------- read methods

    def _hello(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        version = str(params.get("protocol_version", ""))
        if version.split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
            raise ForgeError(ErrorCode.UNSUPPORTED_VERSION,
                             f"protocol {version!r} is not supported; this controller speaks {PROTOCOL_VERSION}")
        project = self.controller.store.project()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "controller_id": self.controller.controller_id,
            "project_id": project.project_id if project else None,
            "actor": {"actor_id": actor.actor_id, "kind": actor.kind},
            "methods": sorted([*self._read_methods, *MUTATIONS]),
            "limits": {"max_body_bytes": MAX_BODY_BYTES, "max_page": MAX_PAGE},
        }

    def _project_open(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        project = self.controller.store.project()
        if project is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "this controller holds no project yet")
        return {"project": project.model_dump(mode="json"),
                "hosts": sorted(self.job_service.hosts) if self.job_service else []}

    def _project_summary(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        from forge.reports.summary import build_summary

        return build_summary(
            self.controller.store, now=self.controller.clock.now(),
            since_seq=int(params.get("since", 0)), as_of_seq=params.get("as_of"),
        ).model_dump(mode="json")

    def _project_resume(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        """What changed since the client last looked, from records only."""
        last_seen = int(params.get("last_seen_event_seq", 0))
        summary = self._project_summary(actor, {"since": last_seen})
        latest = self.controller.store.last_event_seq()
        return {"labels": "recorded and derived; nothing in this briefing is model-generated",
                "missed_events": max(0, latest - last_seen), "summary": summary}

    def _task_list(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(params.get("limit", 50)), MAX_PAGE))
        tasks = self.controller.store.tasks(status=params.get("status"), after_task_id=params.get("cursor"),
                                            limit=limit)
        return {"tasks": [task.model_dump(mode="json") for task in tasks],
                "next_cursor": tasks[-1].task_id if len(tasks) == limit else None,
                "record_version": self.controller.store.last_event_seq()}

    def _task_get(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        store = self.controller.store
        task = store.task(str(params.get("task_id", "")))
        if task is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "task not found")
        return {"task": task.model_dump(mode="json"),
                "jobs": [job.model_dump(mode="json", exclude={"spec"}) for job in store.jobs(task_id=task.task_id)]}

    def _evidence_list(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        store = self.controller.store
        criteria: list[str] = []
        if "task_id" in params:
            task = store.task(str(params["task_id"]))
            if task is None:
                raise ForgeError(ErrorCode.INVALID_REQUEST, "task not found")
            criteria = [ref.id for ref in task.criterion_refs]
        elif "criterion_id" in params:
            criteria = [str(params["criterion_id"])]
        items = []
        for criterion_id in criteria:
            for assessment in store.assessments(criterion_id=criterion_id, snapshot_id=params.get("snapshot_id")):
                items.append({
                    "assessment": assessment.model_dump(mode="json"),
                    "evidence": [store.evidence(e).model_dump(mode="json") for e in assessment.evidence_refs],
                })
        return {"items": items[:MAX_PAGE], "truncated": len(items) > MAX_PAGE}

    def _artifact_read(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        artifact = self.controller.store.artifact(str(params.get("artifact_id", "")))
        if artifact is None or artifact.tombstoned_at is not None:
            raise ForgeError(ErrorCode.ARTIFACT_MISSING, "artifact not found or no longer retained")
        if artifact.sensitivity == "sensitive" and not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "this artifact is readable only by the trusted user")
        offset = max(0, int(params.get("offset", 0)))
        length = max(0, min(int(params.get("length", MAX_ARTIFACT_CHUNK)), MAX_ARTIFACT_CHUNK))
        data = self.controller.blobs.read_bytes(artifact.digest, max_bytes=artifact.size)
        chunk = data[offset:offset + length]
        return {"artifact_id": artifact.artifact_id, "digest": artifact.digest, "size": artifact.size,
                "offset": offset, "data_b64": base64.b64encode(chunk).decode(), "eof": offset + len(chunk) >= artifact.size}

    def _events_list(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(params.get("limit", 100)), MAX_PAGE))
        events = self.controller.store.events(after_seq=int(params.get("after_seq", 0)), limit=limit)
        return {"events": [event.model_dump(mode="json") for event in events],
                "latest_event_seq": self.controller.store.last_event_seq()}

    def _authority_inspect(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        trust, project = self.controller.trust_store, self.controller.store.project()
        if trust is None or project is None:
            return {"grants": [], "source_roots": []}
        grants = [grant.model_dump(mode="json") for grant in trust.effective_grants(project.project_id)]
        roots = [{"label": label, "approved": trust.source_root(project.project_id, label) is not None}
                 for label in project.source_roots]
        return {"grants": grants, "source_roots": roots}

    def _approve_source_root(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        """Trust-store change: only the trusted user, never a model or host."""
        from pathlib import Path

        trust, project = self.controller.trust_store, self.controller.store.project()
        if trust is None or project is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no project or trust store")
        label = str(params.get("label", ""))
        if label not in project.source_roots:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"source root {label!r} is not declared by this project")
        resolved = trust.approve_source_root(project.project_id, label, Path(str(params.get("path", ""))), actor)
        return {"label": label, "path": str(resolved)}

    # ----------------------------------------------------------- compliance

    def _compliance(self):  # noqa: ANN202 - ComplianceService
        from forge.compliance.service import ComplianceService

        return ComplianceService(self.controller)

    @staticmethod
    def _text(params: dict[str, Any], name: str, *, required: bool = False, limit: int = 4000) -> str | None:
        value = params.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _strings(params: dict[str, Any], name: str) -> list[str]:
        value = params.get(name) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{name} must be a list of strings")
        return value

    @staticmethod
    def _options(params: dict[str, Any]) -> dict[str, str] | None:
        value = params.get("options")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "options must be an object")
        return {str(k): str(v) for k, v in value.items()}

    def _compliance_set_domain(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        return self._compliance().set_domain(actor, self._text(params, "domain", required=True, limit=64),
                                             self._options(params), self._strings(params, "catalogs"))

    def _compliance_check(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        from forge.compliance.report import next_steps, render_markdown
        from forge.compliance.service import ComplianceReport

        result = self._compliance().check(self._text(params, "snapshot_id", limit=100),
                                          requirements_path=self._text(params, "requirements", limit=1000),
                                          profile_id=self._text(params, "domain", limit=64),
                                          options=self._options(params))
        report = ComplianceReport.model_validate(result["report"])
        return {**result, "markdown": render_markdown(report), "next": next_steps(report)}

    def _compliance_report(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        from forge.compliance.report import next_steps, render_markdown

        report = self._compliance().report(self._text(params, "snapshot_id", limit=100),
                                           profile_id=self._text(params, "domain", limit=64),
                                           options=self._options(params))
        return {"report": report.model_dump(mode="json"), "markdown": render_markdown(report),
                "next": next_steps(report)}

    def _compliance_attest(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        ids = self._compliance().attest(
            actor, snapshot_id=self._text(params, "snapshot_id", limit=100),
            catalog_id=self._text(params, "catalog", required=True, limit=64),
            labels=self._strings(params, "labels")[:500], statement=str(params.get("statement", ""))[:4000],
            scope=str(params.get("scope", "process")), withdraw=bool(params.get("withdraw", False)))
        return {"evidence_ids": ids}

    def _compliance_import_catalog(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        catalog, mappings = params.get("catalog"), params.get("mappings") or {}
        if not isinstance(catalog, dict) or not isinstance(mappings, dict):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "catalog and mappings must be objects")
        return self._compliance().import_catalog(actor, catalog, mappings)

    def _compliance_import_vulnerabilities(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        records = params.get("records")
        if not isinstance(records, list):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "records must be a list of OSV records")
        return self._compliance().import_vulnerabilities(actor, records,
                                                         source=str(params.get("source", "unspecified")))

    def _health(self, actor: ActorContext, params: dict[str, Any]) -> dict[str, Any]:
        health: dict[str, Any] = {"controller": "ok", "last_event_seq": self.controller.store.last_event_seq()}
        if self.job_service is not None:
            health["pending_dispatch"] = len(self.controller.store.pending_outbox())
            health["hosts"] = {}
            for host_id, host in self.job_service.hosts.items():
                try:
                    health["hosts"][host_id] = host.get_capabilities()["backend"]
                except Exception:  # noqa: BLE001 - reported, not raised
                    health["hosts"][host_id] = {"available": False, "unavailable_reason": "host unreachable"}
        return health


class _Handler(BaseHTTPRequestHandler):
    server_version = "forge"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def api(self) -> ApiServer:
        return self.server.api  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from base class
        return  # requests can contain user data; nothing is written to stderr

    # ---------------------------------------------------------- guard rails

    def _reject(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(status)
        self._common_headers("application/json", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _common_headers(self, content_type: str, length: int | None) -> None:
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _authenticate(self, path_expected: str) -> ActorContext | None:
        path, _, query = self.path.partition("?")
        if path != path_expected:
            self._reject(HTTPStatus.NOT_FOUND, "unknown path")
            return None
        if self.headers.get("Host") not in self.api.allowed_hosts:
            self._reject(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return None
        if self.headers.get("Origin") is not None:
            self._reject(HTTPStatus.FORBIDDEN, "browser origins are not allowed")
            return None
        if path_expected == "/rpc" and query:
            self._reject(HTTPStatus.BAD_REQUEST, "query strings are not accepted")
            return None
        if "token" in query.lower() or "auth" in query.lower():
            self._reject(HTTPStatus.BAD_REQUEST, "credentials are never accepted in URLs")
            return None
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        actor = self.api.actor_for(token) if scheme == "Bearer" and token else None
        if actor is None:
            self._reject(HTTPStatus.UNAUTHORIZED, "missing or invalid credentials")
            return None
        return actor

    # ------------------------------------------------------------------ RPC

    def do_POST(self) -> None:  # noqa: N802 - name required by http.server
        actor = self._authenticate("/rpc")
        if actor is None:
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0:
            self._reject(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
            return
        if length > MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request body exceeds {MAX_BODY_BYTES} bytes")
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        body = self.rfile.read(length)
        self._respond(self._handle_rpc(actor, body))

    def _respond(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._common_headers("application/json", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _handle_rpc(self, actor: ActorContext, body: bytes) -> dict[str, Any]:
        def error(code: int, message: str, request_id: Any = None, data: dict | None = None) -> dict[str, Any]:
            failure: dict[str, Any] = {"code": code, "message": message}
            if data:
                failure["data"] = data
            return {"jsonrpc": "2.0", "id": request_id, "error": failure}

        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return error(JSONRPC_PARSE_ERROR, "request body is not valid JSON")
        if isinstance(request, list):
            return error(JSONRPC_INVALID, "batch requests are not supported")
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return error(JSONRPC_INVALID, "expected a JSON-RPC 2.0 request object")
        request_id = request.get("id")
        if "id" not in request or isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            # Notifications get no answer, so they could never confirm a write.
            return error(JSONRPC_INVALID, "requests must carry an id; notifications are not accepted")
        method, params = request.get("method"), request.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return error(JSONRPC_INVALID, "method must be a string and params an object", request_id)
        correlation_id = new_id("request")
        try:
            result = self.api.dispatch(actor, method, params)
        except KeyError:
            return error(JSONRPC_NO_METHOD, f"unknown method {method[:100]!r}", request_id)
        except ForgeError as failure:
            return error(JSONRPC_FORGE, failure.message, request_id,
                         {**failure.to_dict(), "correlation_id": correlation_id})
        except (TypeError, ValueError):
            return error(JSONRPC_PARAMS, "invalid params", request_id, {"correlation_id": correlation_id})
        except Exception:  # noqa: BLE001 - never leak internals to clients
            return error(JSONRPC_INTERNAL, "internal error", request_id, {"correlation_id": correlation_id})
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    # ------------------------------------------------------------------ SSE

    def do_GET(self) -> None:  # noqa: N802
        actor = self._authenticate("/events")
        if actor is None:
            return
        _, _, query = self.path.partition("?")
        cursor = 0
        for part in query.split("&"):
            key, _, value = part.partition("=")
            if key == "after" and value.isdigit():
                cursor = int(value)
        last_event_id = self.headers.get("Last-Event-ID", "")
        if last_event_id.isdigit():
            cursor = int(last_event_id)

        live: queue.Queue = queue.Queue(maxsize=SSE_QUEUE_LIMIT)
        overflow = threading.Event()

        def listener(events: list) -> None:
            for event in events:
                try:
                    live.put_nowait(event)
                except queue.Full:
                    overflow.set()

        controller = self.api.controller
        with self.api.lock:
            controller.subscribe(listener)
        self.send_response(HTTPStatus.OK)
        self._common_headers("text/event-stream", None)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            # Backfill everything after the cursor, then follow live events.
            while True:
                with self.api.lock:
                    page = controller.store.events(after_seq=cursor, limit=MAX_PAGE)
                if not page:
                    break
                for event in page:
                    self._send_event(event)
                    cursor = event.event_seq
            while not self.api._stopping.is_set():
                if overflow.is_set():
                    self.wfile.write(b"event: reload\ndata: {\"reason\": \"client fell behind\"}\n\n")
                    self.wfile.flush()
                    return
                try:
                    event = live.get(timeout=SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if event.event_seq <= cursor:
                    continue  # already sent during backfill
                self._send_event(event)
                cursor = event.event_seq
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            with self.api.lock:
                controller.unsubscribe(listener)

    def _send_event(self, event) -> None:  # noqa: ANN001 - EventRecord
        data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        self.wfile.write(f"id: {event.event_seq}\nevent: {event.event_type}\ndata: {data}\n\n".encode())
        self.wfile.flush()


def new_token() -> str:
    return secrets.token_urlsafe(32)
