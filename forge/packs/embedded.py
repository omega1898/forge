"""Embedded pack, simulated board (ticket F22, partial).

What can be built and tested without hardware:

- Evidence tiers stay distinct. A result is labelled `host` (unit tests on
  the development machine), `emulator` or `hardware`, and a criterion that
  needs one tier is never satisfied by another: an emulator pass is not a
  board pass.
- A board action needs an approval bound to the exact action: the decision
  carries a digest of {board serial, firmware digest, operation}. A swapped
  board or a different image does not match, and the board itself refuses
  an image addressed to another serial.
- Flashing goes through the F07 external-effect path, so the intent (with
  the board identity) is persisted before the board is touched. If power
  is lost mid-flash, recovery reads the board: a half-written slot is
  `unknown`, which requires a person to reconcile; nothing is re-flashed
  automatically.

No emulator (QEMU) or board protocol adapter is installed here; those are
reported unavailable rather than simulated as real.
"""

from __future__ import annotations

import shutil
from collections import Counter
from typing import Any, Literal

from forge.contracts.digests import digest_json
from forge.hostagent.agent import SimulatedCrash

Tier = Literal["host", "emulator", "hardware"]
FLASH_STEPS = ("erase", "write", "verify", "activate")


def tier_problem(required: Tier, observed: Tier) -> str | None:
    if required == observed:
        return None
    return f"requires {required} evidence; this result comes from the {observed}"


def emulator_available() -> dict[str, Any]:
    found = {name: shutil.which(name) for name in ("qemu-system-arm", "qemu-system-riscv32", "renode")}
    return {"available": any(found.values()), "tools": {k: v for k, v in found.items() if v},
            "reason": "" if any(found.values()) else "no emulator (QEMU or Renode) is installed"}


def board_action_digest(serial: str, firmware_digest: str, operation_id: str) -> str:
    return digest_json({"action": "flash", "serial": serial, "firmware_digest": firmware_digest,
                        "operation_id": operation_id})


def authorize_board_action(store, decision_id: str, serial: str, firmware_digest: str,  # noqa: ANN001
                           operation_id: str) -> str | None:
    """None if an approved decision covers exactly this action; otherwise why not."""
    decision = next((d for d in store.decisions() if d.decision_id == decision_id), None)
    if decision is None:
        return "no such decision"
    if decision.disposition != "approved":
        return f"the decision is {decision.disposition}"
    if decision.proposal_digest != board_action_digest(serial, firmware_digest, operation_id):
        return "the approval was for a different board, image or operation"
    return None


class SimulatedBoard:
    """A two-slot (A/B) board for the external-effect path. `interrupt_at` simulates power loss."""

    def __init__(self, serial: str, *, interrupt_at: str | None = None) -> None:
        self.serial = serial
        self.interrupt_at = interrupt_at
        self.slots: dict[str, dict[str, Any]] = {"A": {"digest": "sha256:" + "0" * 64, "state": "valid"},
                                                 "B": {"digest": None, "state": "empty"}}
        self.active = "A"
        self.operations: dict[str, str] = {}
        self.flash_count: Counter[str] = Counter()

    def apply(self, operation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("target_serial") != self.serial:
            raise ValueError(f"target identity mismatch: image is for {payload.get('target_serial')}, "
                             f"board is {self.serial}")
        digest = str(payload["firmware_digest"])
        slot = "B" if self.active == "A" else "A"
        self.flash_count[operation_id] += 1
        for step in FLASH_STEPS:
            if step == "erase":
                self.slots[slot] = {"digest": None, "state": "partial"}
            if self.interrupt_at == step:
                raise SimulatedCrash(f"power lost during {step}")
            if step == "write":
                self.slots[slot]["digest"] = digest
            elif step == "verify":
                self.slots[slot]["state"] = "valid"
            elif step == "activate":
                self.active = slot
        self.operations[operation_id] = digest
        return {"serial": self.serial, "slot": slot, "firmware_digest": digest}

    def lookup(self, operation_id: str) -> Literal["applied", "absent", "unknown"]:
        if operation_id in self.operations and self.slots[self.active]["digest"] == self.operations[operation_id]:
            return "applied"
        if any(slot["state"] == "partial" for slot in self.slots.values()):
            return "unknown"   # something was half-written; it may or may not be this operation
        return "absent"

    def inspect(self) -> dict[str, Any]:
        return {"serial": self.serial, "active": self.active,
                "slots": {name: dict(slot) for name, slot in self.slots.items()}}
