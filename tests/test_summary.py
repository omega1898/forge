"""Ticket D01: project.summary (the standup skill) as deterministic code.

Acceptance: the summary for a seeded project lists completed, running,
blocked-with-reason and pending decisions exactly; no model is involved; a
summary rebuilt by replaying the event log is identical to one read from the
live tables.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from forge.contracts.clock import FixedClock, format_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.record.db import write_transaction
from forge.record.events import DomainEvent, append_event
from forge.record.projections import apply_event
from forge.reports.standup import escape, render_standup
from forge.reports.summary import build_summary
from tests.conftest import World


def force_status(world: World, task_id: str, to_status: str, reason: str, clock: FixedClock) -> None:
    """Append a status change directly, bypassing the controller's gates.

    Stands in for the completion evaluator (tickets F05/F06), which will be
    the only writer allowed to mark a task completed. Used here only so the
    summary can be tested against completed tasks before that evaluator exists.
    """
    task = world.store.task(task_id)
    conn = world.store.conn
    with write_transaction(conn):
        record = append_event(
            conn,
            event=DomainEvent(
                "task.status_changed",
                {
                    "task_id": task_id,
                    "from_status": task.status,
                    "to_status": to_status,
                    "reason": reason,
                    "record_version": task.record_version + 1,
                },
            ),
            project_id=world.project_id,
            command_id=new_id("command"),
            actor_id=world.actor.actor_id,
            occurred_at=format_timestamp(clock.now()),
        )
        apply_event(conn, record)


def move(world: World, task_id: str, to_status: str, reason: str) -> None:
    task = world.store.task(task_id)
    world.send(
        "task.transition",
        {"task_id": task_id, "to_status": to_status, "reason": reason},
        expected=task.record_version,
    )


@pytest.fixture
def seeded(world: World, clock: FixedClock) -> dict[str, str]:
    """A project with one task in every interesting state."""
    ids = {
        "done": world.task("Write the format specification"),
        "running": world.task("Collect example packets"),
        "blocked": world.task("Decide byte order"),
        "waiting_user": world.task("Choose error format"),
        "cancelled": world.task("Prototype in Rust"),
    }
    ids["ready"] = world.task("Write boundary fixtures", depends_on=[ids["done"]])
    ids["waiting_tasks"] = world.task(
        "Implement parser", task_type="execute", depends_on=[ids["done"], ids["blocked"]]
    )
    move(world, ids["done"], "running", "starting")
    force_status(world, ids["done"], "completed", "specification reviewed", clock)
    move(world, ids["running"], "running", "starting")
    move(world, ids["blocked"], "blocked", "needs the packet vendor's byte-order answer")
    move(world, ids["waiting_user"], "waiting_for_user", "two error formats are acceptable")
    move(world, ids["cancelled"], "cancelled", "Python is enough")
    ids["decision"] = world.send(
        "decision.request",
        {
            "decision_kind": "architecture",
            "subject_ref": {"id": ids["waiting_user"], "revision": 2},
            "question": "JSON errors or exit codes only?",
        },
    ).result["decision_id"]
    return ids


def test_summary_lists_every_state_exactly(world: World, seeded: dict[str, str], clock: FixedClock) -> None:
    summary = build_summary(world.store, now=clock.now())

    assert [item.task_id for item in summary.done.items] == [seeded["done"]]
    assert summary.done.items[0].reason == "specification reviewed"
    assert [item.task_id for item in summary.running.items] == [seeded["running"]]
    assert [item.task_id for item in summary.ready.items] == [seeded["ready"]]
    assert [item.task_id for item in summary.blocked.items] == [seeded["blocked"]]
    assert summary.blocked.items[0].reason == "needs the packet vendor's byte-order answer"
    assert [item.task_id for item in summary.waiting_for_user.items] == [seeded["waiting_user"]]
    assert [item.task_id for item in summary.ended.items] == [seeded["cancelled"]]

    waiting = summary.waiting_on_tasks.items
    assert [item.task_id for item in waiting] == [seeded["waiting_tasks"]]
    assert waiting[0].waiting_on == [seeded["blocked"]]  # the completed dependency is not listed

    assert [item.decision_id for item in summary.pending_decisions.items] == [seeded["decision"]]
    assert summary.status_counts == {
        "queued": 2, "running": 1, "blocked": 1, "waiting_for_user": 1,
        "completed": 1, "failed": 0, "cancelled": 1,
    }


def test_replay_gives_the_identical_summary(world: World, seeded: dict[str, str], clock: FixedClock) -> None:
    live = build_summary(world.store, now=clock.now())
    replayed = build_summary(world.store, now=clock.now(), force_replay=True)
    assert replayed == live
    assert replayed.model_dump_json() == live.model_dump_json()


def test_summary_at_an_earlier_event_shows_the_earlier_state(
    world: World, seeded: dict[str, str], clock: FixedClock
) -> None:
    # Find the event that blocked the task, then look at the moment before it.
    blocked_at = next(
        event.event_seq
        for event in world.store.iter_events(event_types=("task.status_changed",))
        if event.payload["task_id"] == seeded["blocked"]
    )
    before = build_summary(world.store, now=clock.now(), as_of_seq=blocked_at - 1)
    after = build_summary(world.store, now=clock.now(), as_of_seq=blocked_at)

    assert seeded["blocked"] not in [item.task_id for item in before.blocked.items]
    assert seeded["blocked"] in [item.task_id for item in before.ready.items]
    assert [item.task_id for item in after.blocked.items] == [seeded["blocked"]]
    # The live tables were not touched by looking into the past.
    assert world.store.verify_projections() == []


def test_window_reports_only_changes_since_the_last_standup(
    world: World, seeded: dict[str, str], clock: FixedClock
) -> None:
    latest = world.store.last_event_seq()
    quiet = build_summary(world.store, now=clock.now(), since_seq=latest)
    assert quiet.done.total == 0
    assert quiet.ended.total == 0
    assert quiet.activity == {}
    # Current state is still reported in full: a blocker does not vanish
    # because it was already blocked yesterday.
    assert quiet.blocked.total == 1
    assert quiet.pending_decisions.total == 1


def test_blocked_reason_is_the_most_recent_one(world: World, clock: FixedClock) -> None:
    task_id = world.task()
    move(world, task_id, "blocked", "waiting for sample files")
    move(world, task_id, "queued", "sample files arrived")
    move(world, task_id, "blocked", "sample files are corrupt")
    [line] = build_summary(world.store, now=clock.now()).blocked.items
    assert line.reason == "sample files are corrupt"


def test_expired_decisions_are_flagged_against_the_given_time(world: World, clock: FixedClock) -> None:
    task_id = world.task()
    expires = clock.now() + timedelta(hours=1)
    world.send(
        "decision.request",
        {
            "decision_kind": "architecture",
            "subject_ref": {"id": task_id, "revision": 1},
            "question": "q",
            "expires_at": format_timestamp(expires),
        },
    )
    assert build_summary(world.store, now=clock.now()).pending_decisions.items[0].expired is False
    later = build_summary(world.store, now=expires + timedelta(seconds=1))
    assert later.pending_decisions.items[0].expired is True
    assert "EXPIRED" in render_standup(later)


def test_recorded_decisions_appear_in_the_window(world: World, seeded: dict[str, str], clock: FixedClock) -> None:
    world.send(
        "decision.record",
        {"decision_id": seeded["decision"], "disposition": "approved", "reason": "JSON is easier to test"},
        expected=1,
    )
    summary = build_summary(world.store, now=clock.now())
    assert summary.pending_decisions.total == 0
    assert [(item.decision_id, item.disposition) for item in summary.decided.items] == [
        (seeded["decision"], "approved")
    ]


def test_sections_are_bounded_and_say_how_many_are_hidden(world: World, clock: FixedClock) -> None:
    for number in range(5):
        world.task(f"task {number}")
    summary = build_summary(world.store, now=clock.now(), limit=2)
    assert summary.ready.total == 5
    assert len(summary.ready.items) == 2
    assert "…and 3 more" in render_standup(summary)


@pytest.mark.parametrize(
    ("since", "as_of"), [(-1, None), (5, 2), (0, 999), (0, -1)]
)
def test_out_of_range_windows_are_rejected(world: World, clock: FixedClock, since: int, as_of: int | None) -> None:
    world.task()
    with pytest.raises(ForgeError) as caught:
        build_summary(world.store, now=clock.now(), since_seq=since, as_of_seq=as_of)
    assert caught.value.code == ErrorCode.INVALID_REQUEST


def test_empty_database_has_no_summary(store, clock: FixedClock) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError, match="no project"):
        build_summary(store, now=clock.now())


# --------------------------------------------------------------- rendering --


def test_standup_lists_blockers_with_reasons_and_ids(world: World, seeded: dict[str, str], clock: FixedClock) -> None:
    text = render_standup(build_summary(world.store, now=clock.now()))
    assert f"(`{seeded['blocked']}`): needs the packet vendor's byte-order answer" in text
    assert f"Running: Collect example packets (`{seeded['running']}`)" in text
    assert "Waiting on 1 unfinished task: Implement parser" in text
    assert f"(`{seeded['decision']}`)" in text


@pytest.mark.parametrize(
    "hostile",
    [
        "[click me](javascript:alert(1))",
        "![tracker](http://example.invalid/pixel.png)",
        "<img src=x onerror=alert(1)>",
        "a | b | c",
        "first line\n## Fake heading\n- fake bullet",
        "`code` **bold** _em_",
    ],
)
def test_untrusted_text_cannot_inject_markdown(hostile: str) -> None:
    rendered = escape(hostile)
    assert "\n" not in rendered
    assert "<" not in rendered and ">" not in rendered  # HTML can never start
    for structure in ("](", "![", "**", "`code`"):
        assert structure not in rendered
    assert " | " not in rendered.replace("\\|", "")


def test_entities_in_input_stay_literal() -> None:
    # Someone typing "&lt;b&gt;" should see exactly that, not a decoded "<b>".
    assert escape("&lt;b&gt;") == "&amp;lt;b&amp;gt;"


def test_long_text_is_truncated() -> None:
    assert len(escape("x" * 10_000)) <= 160


def test_hostile_title_in_a_real_report(world: World, clock: FixedClock) -> None:
    world.task("Fix [it](http://evil.invalid)\n### Injected")
    text = render_standup(build_summary(world.store, now=clock.now()))
    assert "### Injected" not in text
    assert "](http" not in text
