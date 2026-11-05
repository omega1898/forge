"""Start a controller and its local API for one project directory.

The launcher is trusted installation code. It creates a random per-client
token and writes the connection details to a file readable only by the
current user (mode 0600). The CLI reads that file; the token never appears
in a URL, a command line, or the project directory.

Layout under a FORGE root:
    project/state.db         the project record
    project/blobs/           artifacts
    user/trust.db            grants and approved source roots
    user/run/api.json        connection file for the running API (0600)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from forge.api.server import ApiServer, new_token
from forge.authority.grants import TrustStore
from forge.contracts.clock import Clock, SystemClock
from forge.contracts.commands import ActorContext
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.controller.service import Controller
from forge.record.store import ProjectStore


def connection_file(root: Path) -> Path:
    return Path(root) / "user" / "run" / "api.json"


def write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle)


def read_connection(root: Path) -> dict:
    path = connection_file(root)
    if not path.exists():
        raise ForgeError(ErrorCode.RESOURCE_UNAVAILABLE, "no running FORGE controller for this root (start one with: forge serve)")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ForgeError(ErrorCode.PERMISSION_DENIED, f"{path} is readable by other users; refusing to use it")
    return json.loads(path.read_text())


class Launched:
    def __init__(self, store: ProjectStore, trust: TrustStore, controller: Controller, server: ApiServer,
                 token: str, actor: ActorContext, root: Path) -> None:
        self.store, self.trust, self.controller, self.server = store, trust, controller, server
        self.token, self.actor, self.root = token, actor, root

    def close(self) -> None:
        self.server.stop()
        connection_file(self.root).unlink(missing_ok=True)
        self.trust.close()
        self.store.close()


def launch(root: Path, *, port: int = 0, clock: Clock | None = None, job_service_factory=None) -> Launched:  # noqa: ANN001
    root = Path(root)
    clock = clock or SystemClock()
    store = ProjectStore.open(root / "project" / "state.db", clock)
    trust = TrustStore.open(root / "user" / "trust.db", clock)
    controller = Controller(store, clock=clock, trust_store=trust)
    # The person who launched FORGE on their own machine is the trusted user.
    actor = ActorContext(actor_id=new_id("actor"), kind="human", channel="trusted_local")
    token = new_token()
    job_service = job_service_factory(controller) if job_service_factory else None
    server = ApiServer(controller, tokens={token: actor}, port=port, job_service=job_service,
                       work_root=root / "work")
    server.start()
    write_private(connection_file(root), {"url": server.url, "token": token, "actor_id": actor.actor_id})
    return Launched(store, trust, controller, server, token, actor, root)
