"""Machine-readable JSON Schemas for the contracts, shipped inside the package.

The Python models are the single source of truth. The JSON files in
forge/contracts/schemas/ are generated from them and committed so that:

- a fresh, disconnected installation can load them (no network fetch), and
- non-Python clients (the future editor extension) can validate the same shapes.

A test regenerates the schemas and fails if the committed files drifted.

Regenerate after changing a model:
    python -m forge.contracts.schemas --write
"""

from __future__ import annotations

import argparse
import json
from importlib import resources
from pathlib import Path
from typing import Any

from forge.contracts import commands, records
from forge.contracts.errors import ErrorCode, ForgeError, safe_preview

SCHEMA_MODELS = {
    "command_envelope": commands.CommandEnvelope,
    "command_receipt": commands.CommandReceipt,
    "params.project.create": commands.ProjectCreateParams,
    "params.requirement.create": commands.RequirementCreateParams,
    "params.criterion.create": commands.CriterionCreateParams,
    "params.task.create": commands.TaskCreateParams,
    "params.task.add_dependency": commands.TaskAddDependencyParams,
    "params.task.transition": commands.TaskTransitionParams,
    "params.decision.request": commands.DecisionRequestParams,
    "params.decision.record": commands.DecisionRecordParams,
    "record.project": records.Project,
    "record.requirement": records.Requirement,
    "record.criterion": records.Criterion,
    "record.task": records.Task,
    "record.decision": records.Decision,
    "record.event": records.EventRecord,
}

_SCHEMA_DIR = "schemas"


def generate_schemas() -> dict[str, dict[str, Any]]:
    generated = {}
    for name, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"forge:contracts/{name}"
        generated[name] = schema
    return generated


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def load_schema(name: str) -> dict[str, Any]:
    """Load a packaged schema by name, using only local package files."""
    if name not in SCHEMA_MODELS:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"unknown schema {safe_preview(name)}")
    resource = resources.files("forge.contracts").joinpath(_SCHEMA_DIR, f"{name}.json")
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ForgeError(
            ErrorCode.INTERNAL_ERROR,
            f"packaged schema {name} is missing; the installation is incomplete",
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the committed schema files")
    args = parser.parse_args(argv)
    target = Path(__file__).parent / _SCHEMA_DIR
    target.mkdir(exist_ok=True)
    drifted = []
    for name, schema in generate_schemas().items():
        path = target / f"{name}.json"
        text = render(schema)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            drifted.append(name)
            if args.write:
                path.write_text(text, encoding="utf-8", newline="\n")
    if drifted and not args.write:
        print("schemas out of date: " + ", ".join(drifted))
        return 1
    print("wrote: " + ", ".join(drifted) if drifted else "schemas up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
