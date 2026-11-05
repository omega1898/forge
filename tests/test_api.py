"""Ticket F08: authenticated local API, event stream, CLI client.

Acceptance: the CLI can open/resume, submit a task, inspect evidence and
cancel; a duplicate mutation key has one effect. Security: loopback alone is
not authentication; Host, Origin, token, URL-credential and size checks hold.
"""

from __future__ import annotations

import http.client
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from forge.api.client import ForgeClient, RpcError
from forge.api.launcher import connection_file, launch, read_connection
from forge.api.server import MAX_BODY_BYTES
from forge.client_cli import main as cli
from forge.contracts.clock import FixedClock
from forge.contracts.ids import new_id


@pytest.fixture
def running(tmp_path: Path):  # noqa: ANN201
    root = tmp_path / "forge-root"
    launched = launch(root, clock=FixedClock())
    client = ForgeClient(launched.server.url, launched.token)
    project = client.call("project.create", {"idempotency_key": "create", "params": {
        "name": "Packet reader", "objective": "Read packets; reject incomplete ones", "source_roots": ["main"]}})
    yield {"launched": launched, "client": client, "root": root, "project_id": project["project_id"]}
    launched.close()


def raw(launched, *, method: str = "POST", path: str = "/rpc", body: bytes = b"", headers: dict | None = None):  # noqa: ANN001, ANN201
    connection = http.client.HTTPConnection("127.0.0.1", launched.server.port, timeout=10)
    base = {"Host": f"127.0.0.1:{launched.server.port}", "Authorization": f"Bearer {launched.token}",
            "Content-Type": "application/json"}
    base.update(headers or {})
    for key in [k for k, v in base.items() if v is None]:
        del base[key]
    connection.request(method, path, body=body, headers=base)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, payload


def rpc_body(method: str, params: dict | None = None, **extra: object) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}, **extra}).encode()


# --------------------------------------------------------------- security --


def test_connection_file_is_private(running) -> None:  # noqa: ANN001
    path = connection_file(running["root"])
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    os.chmod(path, 0o644)
    with pytest.raises(Exception, match="readable by other users"):
        read_connection(running["root"])


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Authorization": None}, 401),
        ({"Authorization": "Bearer wrong-token"}, 401),
        ({"Authorization": "Basic Zm9vOmJhcg=="}, 401),
        ({"Host": "evil.example:80"}, 403),                 # DNS rebinding
        ({"Origin": "https://evil.example"}, 403),          # a web page calling localhost
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_requests_failing_a_guard_are_refused(running, headers: dict, status: int) -> None:  # noqa: ANN001
    code, _ = raw(running["launched"], body=rpc_body("project.open"), headers=headers)
    assert code == status


def test_credentials_in_urls_are_refused(running) -> None:  # noqa: ANN001
    launched = running["launched"]
    code, _ = raw(launched, path=f"/rpc?token={launched.token}", body=rpc_body("project.open"),
                  headers={"Authorization": None})
    assert code == 400
    code, _ = raw(launched, method="GET", path=f"/events?token={launched.token}", headers={"Authorization": None})
    assert code == 400


def test_oversized_body_is_refused(running) -> None:  # noqa: ANN001
    code, _ = raw(running["launched"], body=b" " * (MAX_BODY_BYTES + 1))
    assert code == 413


def test_unknown_paths_are_not_found(running) -> None:  # noqa: ANN001
    assert raw(running["launched"], path="/admin", body=rpc_body("project.open"))[0] == 404


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"{not json", -32700),
        (b"[]", -32600),                                                              # batches
        (json.dumps({"jsonrpc": "2.0", "method": "task.list"}).encode(), -32600),     # notification
        (json.dumps({"jsonrpc": "1.0", "id": 1, "method": "task.list"}).encode(), -32600),
        (rpc_body("system.shutdown"), -32601),
        (rpc_body("job.observe"), -32601),                                            # system-only, not exposed
        (rpc_body("assessment.record"), -32601),
    ],
)
def test_malformed_and_forbidden_rpc_calls(running, body: bytes, code: int) -> None:  # noqa: ANN001
    status, payload = raw(running["launched"], body=body)
    assert status == 200
    assert json.loads(payload)["error"]["code"] == code


def test_errors_carry_codes_not_internals(running) -> None:  # noqa: ANN001
    client = running["client"]
    with pytest.raises(RpcError) as caught:
        client.mutate("task.cancel", running["project_id"], {"task_id": new_id("task"), "reason": "x"})
    error = caught.value
    assert error.forge_code == "invalid_request"
    assert error.data["correlation_id"].startswith("request_")
    assert "Traceback" not in json.dumps(error.data)


def test_session_hello_rejects_unsupported_protocols(running) -> None:  # noqa: ANN001
    client = running["client"]
    hello = client.call("session.hello", {"protocol_version": "1.0"})
    assert "intent.submit" in hello["methods"] and "job.observe" not in hello["methods"]
    with pytest.raises(RpcError) as caught:
        client.call("session.hello", {"protocol_version": "2.0"})
    assert caught.value.forge_code == "unsupported_version"


# ------------------------------------------------------------ idempotency --


def test_duplicate_mutation_key_has_one_effect(running) -> None:  # noqa: ANN001
    client = running["client"]
    params = {"text": "Build a utility that reads a packet file."}
    first = client.mutate("intent.submit", running["project_id"], params, key="intent-1")
    # A transport retry: new JSON-RPC id, same idempotency key and content.
    again = client.mutate("intent.submit", running["project_id"], params, key="intent-1")
    assert again["command_id"] == first["command_id"] and again["duplicate_delivery"] is True
    tasks = client.call("task.list")["tasks"]
    assert len(tasks) == 1


# ---------------------------------------------------------------- events --


def test_event_stream_backfills_then_follows_live(running) -> None:  # noqa: ANN001
    client = running["client"]
    received: list[dict] = []

    def follow() -> None:
        for event in ForgeClient(running["launched"].server.url, running["launched"].token).events(after=0):
            received.append(event)
            if len(received) >= 3:
                return

    reader = threading.Thread(target=follow, daemon=True)
    reader.start()
    client.mutate("intent.submit", running["project_id"], {"text": "first"})
    reader.join(10)
    assert [event["event_seq"] for event in received] == [1, 2, 3]
    assert received[0]["event_type"] == "project.created"
    assert received[1]["event_type"] == "task.created"


def test_reconnect_resumes_after_the_last_seen_event(running) -> None:  # noqa: ANN001
    client = running["client"]
    client.mutate("intent.submit", running["project_id"], {"text": "a"})
    client.mutate("intent.submit", running["project_id"], {"text": "b"})
    stream = ForgeClient(running["launched"].server.url, running["launched"].token).events(after=3)
    first = next(stream)
    assert first["event_seq"] == 4


# ------------------------------------------------------------------- CLI --


def test_cli_open_resume_submit_inspect_and_cancel(running, capsys) -> None:  # noqa: ANN001
    root = str(running["root"])
    assert cli(["open", "--root", root]) == 0
    assert "Packet reader" in capsys.readouterr().out

    assert cli(["intent", "--root", root, "Build the packet reader CLI", "--key", "k1"]) == 0
    out = capsys.readouterr().out
    task_id = out.split("planning task ")[1].split()[0]
    assert cli(["intent", "--root", root, "Build the packet reader CLI", "--key", "k1"]) == 0
    assert "repeat delivery" in capsys.readouterr().out

    assert cli(["tasks", "--root", root]) == 0
    assert task_id in capsys.readouterr().out
    assert cli(["evidence", "--root", root, task_id]) == 0
    assert "no assessments" in capsys.readouterr().out
    assert cli(["resume", "--root", root, "--since", "1"]) == 0
    assert "event(s) since #1" in capsys.readouterr().out

    assert cli(["cancel", "--root", root, task_id, "--reason", "changed my mind"]) == 0
    assert "cancelled" in capsys.readouterr().out
    assert cli(["task", "--root", root, task_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["task"]["status"] == "cancelled"
    assert cli(["cancel", "--root", root, task_id, "--reason", "again"]) == 1
    assert "already cancelled" in capsys.readouterr().err


def test_cli_and_api_reach_the_same_state(running, capsys) -> None:  # noqa: ANN001
    root = str(running["root"])
    cli(["intent", "--root", root, "via cli"])
    running["client"].mutate("intent.submit", running["project_id"], {"text": "via api"})
    capsys.readouterr()
    events = running["client"].call("events.list", {"after_seq": 1})["events"]
    kinds = [event["event_type"] for event in events]
    assert kinds == ["task.created", "intent.submitted", "task.created", "intent.submitted"]


def test_source_root_approval_and_capture_through_the_api(running, tmp_path, capsys) -> None:  # noqa: ANN001
    source = tmp_path / "packet-reader"
    source.mkdir()
    (source / "reader.py").write_text("print('hi')\n")
    client = running["client"]
    client.call("authority.approve_source_root", {"label": "main", "path": str(source)})
    assert cli(["capture", "--root", str(running["root"])]) == 0
    assert "snapshot snapshot_" in capsys.readouterr().out
    roots = client.call("authority.inspect")["source_roots"]
    assert roots == [{"label": "main", "approved": True}]
