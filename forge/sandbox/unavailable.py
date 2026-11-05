"""Backends FORGE knows about but cannot use on this host.

Each reports exactly why it is unavailable, so dispatch fails closed with a
reason instead of silently falling back to weaker isolation on the user's
workstation. Podman is the blueprint's intended first Linux backend; the
Windows and macOS backends are disposable VMs, which need host
virtualization this build environment does not have.
"""

from __future__ import annotations

import platform
import shutil
import sys

from forge.contracts.errors import ErrorCode, ForgeError
from forge.sandbox.base import BackendCapabilities, ExecutionObservation, SandboxSpec


class _ReportOnly:
    backend_id = "unknown"
    target_os = "linux"
    hostile_ok = False

    def _reason(self) -> str:
        raise NotImplementedError

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id=self.backend_id,
            available=False,
            unavailable_reason=self._reason(),
            os=self.target_os,
            arch=platform.machine() or "unknown",
            capabilities=[],
            suitable_for_hostile_code=self.hostile_ok,
        )

    def run(self, spec: SandboxSpec, work_dir) -> ExecutionObservation:  # noqa: ANN001
        raise ForgeError(ErrorCode.MISSING_CAPABILITY, f"{self.backend_id}: {self._reason()}")

    def cancel(self, job_key: str) -> bool:
        return False


class PodmanBackend(_ReportOnly):
    backend_id = "podman_rootless"

    def _reason(self) -> str:
        if shutil.which("podman") is None:
            return "podman is not installed"
        return "podman profile not admitted yet: the pinned image and admission checks are pending"


class BubblewrapBackend(_ReportOnly):
    backend_id = "bubblewrap"

    def _reason(self) -> str:
        if shutil.which("bwrap") is None:
            return "bubblewrap (bwrap) is not installed"
        return "bubblewrap profile not admitted yet"


class WindowsVmBackend(_ReportOnly):
    backend_id = "windows_vm"
    target_os = "windows"
    hostile_ok = True

    def _reason(self) -> str:
        if sys.platform != "win32":
            return "native Windows evidence requires an enrolled Windows host"
        return "Windows VM image and admission checks are not configured"


class MacosVmBackend(_ReportOnly):
    backend_id = "macos_vm"
    target_os = "macos"
    hostile_ok = True

    def _reason(self) -> str:
        if sys.platform != "darwin":
            return "native macOS evidence requires an enrolled Apple-hardware host"
        return "macOS VM restore image and admission checks are not configured"
