"""validate_plan: check a proposed plan change before any of it is committed.

A plan change ("delta") adds tasks, adds dependencies, and may supersede
existing tasks. It is applied atomically or not at all. Validation collects
every problem rather than stopping at the first, so a proposer (a person or
a model) sees the whole picture and a rejected plan never half-runs.

Checks: the delta is based on the current plan revision; keys are unique;
every reference resolves (dependencies, requirements and criteria at their
current revisions, snapshot inputs, grants); budgets are within limits; no
dependency targets a finished task; superseded tasks exist and are not
already superseded; and the combined graph has no cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from forge.authority.grants import TrustStore
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import id_prefix, new_id
from forge.contracts.records import TERMINAL_TASK_STATUSES, Project
from forge.engine.graph import find_cycle
from forge.record.store import RecordReader

MAX_TOTAL_WALL_MS = 7 * 24 * 3600 * 1000


@dataclass
class ValidatedPlan:
    key_to_id: dict[str, str]
    new_edges_on_existing: list[tuple[str, str]]


def validate_plan(
    reader: RecordReader,
    project: Project,
    params,  # noqa: ANN001 - PlanApplyParams
    *,
    trust_store: TrustStore | None,
    now: datetime,
) -> ValidatedPlan:
    current = reader.conn.execute("SELECT plan_revision FROM projects").fetchone()[0]
    if params.base_plan_revision != current:
        raise ForgeError(
            ErrorCode.STALE_VERSION,
            f"the plan is at revision {current}, but this change was based on {params.base_plan_revision}",
            details={"expected": params.base_plan_revision, "current": current},
        )
    errors: list[str] = []
    keys = [task.key for task in params.add_tasks]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        errors.append(f"duplicate task keys: {', '.join(duplicates)}")
    key_to_id = {task.key: new_id("task") for task in params.add_tasks}

    def resolve(reference: str) -> str | None:
        if reference in key_to_id:
            return key_to_id[reference]
        if reference.startswith("task_") and reader.task(reference) is not None:
            return reference
        return None

    total_wall = 0
    superseded: set[str] = set()
    edges: list[tuple[str, str]] = []
    for task in params.add_tasks:
        label = f"task {task.key!r}"
        total_wall += task.budget.max_wall_ms
        for dependency in task.depends_on:
            target = resolve(dependency)
            if target is None:
                errors.append(f"{label} depends on unknown task {dependency!r}")
            elif target == key_to_id[task.key]:
                errors.append(f"{label} depends on itself")
            else:
                edges.append((key_to_id[task.key], target))
        for ref in task.requirement_refs:
            errors.extend(_ref_problems(reader, ref, "requirement", label))
        for ref in task.criterion_refs:
            errors.extend(_ref_problems(reader, ref, "criterion", label))
        for ref in task.input_refs:
            if id_prefix(ref.id) != "snapshot" or reader.snapshot(ref.id) is None:
                errors.append(f"{label} input {ref.id} is not an existing snapshot")
        if task.authority_ref is not None:
            if trust_store is None or trust_store.effective_grant(task.authority_ref, project.project_id) is None:
                errors.append(f"{label} cites {task.authority_ref}, which is not an approved, unexpired grant")
        if task.supersedes_task_id is not None:
            old = reader.task(task.supersedes_task_id)
            if old is None:
                errors.append(f"{label} supersedes unknown task {task.supersedes_task_id}")
            elif old.superseded_by_task_id is not None or task.supersedes_task_id in superseded:
                errors.append(f"{label}: {task.supersedes_task_id} is already superseded")
            superseded.add(task.supersedes_task_id)
    if total_wall > MAX_TOTAL_WALL_MS:
        errors.append(f"total wall-clock budget {total_wall} ms exceeds the {MAX_TOTAL_WALL_MS} ms limit")

    new_edges_on_existing: list[tuple[str, str]] = []
    for link in params.add_dependencies:
        source, target = resolve(link.task), resolve(link.depends_on)
        if source is None or target is None:
            errors.append(f"dependency {link.task!r} -> {link.depends_on!r} names an unknown task")
            continue
        if source == target:
            errors.append(f"dependency {link.task!r} -> itself")
            continue
        existing_source = reader.task(source)
        if existing_source is not None:
            if existing_source.status in TERMINAL_TASK_STATUSES:
                errors.append(f"{source} is {existing_source.status}; finished tasks are not re-planned")
            if target in existing_source.depends_on:
                errors.append(f"{source} already depends on {target}")
            new_edges_on_existing.append((source, target))
        edges.append((source, target))

    cycle = find_cycle(reader.dependency_edges() + edges) if not errors else None
    if cycle is not None:
        labels = {value: key for key, value in key_to_id.items()}
        errors.append("dependency cycle: " + " -> ".join(labels.get(node, node) for node in cycle))
    if errors:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"plan rejected with {len(errors)} problem(s): {errors[0]}",
            details={"errors": errors},
        )
    return ValidatedPlan(key_to_id=key_to_id, new_edges_on_existing=new_edges_on_existing)


def _ref_problems(reader: RecordReader, ref, kind: str, label: str) -> list[str]:  # noqa: ANN001
    if id_prefix(ref.id) != kind:
        return [f"{label} lists {ref.id} as a {kind}"]
    record = reader.requirement(ref.id) if kind == "requirement" else reader.criterion(ref.id)
    if record is None:
        return [f"{label} cites unknown {kind} {ref.id}"]
    if record.revision != ref.revision:
        return [f"{label} cites {ref.id} revision {ref.revision}, current is {record.revision}"]
    return []
