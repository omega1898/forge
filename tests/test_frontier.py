"""Hybrid model layer: frontier adapter, escalation packages, refusal recording.

No network is used: a stand-in for the Anthropic client records requests
and returns scripted messages. One test calls the real API, and runs only
when ANTHROPIC_API_KEY is set.
"""

from __future__ import annotations

import json
import os
from collections import deque
from types import SimpleNamespace

import pytest

from forge.amplifier.loop import AssistReport
from forge.contracts.errors import ForgeError
from forge.gateway.adapters import FakeModelAdapter
from forge.gateway.contracts import Message, ModelConfig
from forge.gateway.escalation import EscalationService, explain_gap, files_from_snapshot, render_for_review
from forge.gateway.frontier import DEFAULT_FRONTIER_MODEL, FALLBACK_BETA, AnthropicAdapter, strict_schema
from forge.gateway.proposals import AgentProposal
from forge.gateway.schema_subset import flat_schema
from forge.gateway.service import ModelGateway
from tests.test_proposals import BUGGY, CORRECT, _assistant, _task, diff, needs_sandbox, rig  # noqa: F401


def message(text: str = "", stop_reason: str = "end_turn", category: str | None = None,
            explanation: str | None = None) -> SimpleNamespace:
    details = SimpleNamespace(category=category, explanation=explanation) if stop_reason == "refusal" else None
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop_reason, stop_details=details,
                           usage=SimpleNamespace(input_tokens=120, output_tokens=40))


class FakeAnthropic:
    """Records each request; returns scripted final messages in order."""

    def __init__(self, *replies: SimpleNamespace) -> None:
        self.replies, self.requests = deque(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.messages = SimpleNamespace(count_tokens=lambda **params: SimpleNamespace(input_tokens=321))

    def _stream(self, **params):  # noqa: ANN003, ANN202
        self.requests.append(params)
        reply = self.replies.popleft()

        class Stream:
            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, *exc) -> None:  # noqa: ANN002
                return None

            def get_final_message(self):  # noqa: ANN202
                return reply
        return Stream()


PROBE = [message("ready"), message('{"answer": "yes"}')]


def frontier_gateway(controller, *replies: SimpleNamespace, effort: str = "high"):  # noqa: ANN001, ANN201
    client = FakeAnthropic(*PROBE, *replies)
    gateway = ModelGateway(controller, adapters={"anthropic": AnthropicAdapter(effort=effort,
                                                                               client_factory=lambda: client),
                                                 "fake": FakeModelAdapter([{"kind": "text", "text": "ready"},
                                                                           {"answer": "yes"}])})
    gateway.register(ModelConfig(model_config_id="frontier", provider_type="anthropic", model_name=DEFAULT_FRONTIER_MODEL,
                                 runtime_version="api", context_capacity=1_000_000, max_output_tokens=64_000))
    gateway.probe("frontier")
    return gateway, client


# ------------------------------------------------------------------ adapter


def test_requests_use_adaptive_thinking_effort_structured_output_and_fallback(world) -> None:  # noqa: ANN001
    proposal = {"action": "observe", "rationale": "read it", "tool_id": "read_file", "tool_arguments": ["a.py"]}
    gateway, client = frontier_gateway(world.controller, message(json.dumps(proposal)), effort="xhigh")
    result = gateway.generate(model_config_id="frontier", slot_id="next_step",
                              messages=[Message(role="system", content="be exact"), Message(role="user", content="go")],
                              output_model=AgentProposal, output_schema=flat_schema(AgentProposal))
    assert result.status == "valid" and result.output["tool_id"] == "read_file"
    sent = client.requests[-1]
    assert sent["model"] == "claude-opus-5-5" and sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["effort"] == "xhigh"
    assert sent["betas"] == [FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert sent["system"] == "be exact" and sent["messages"] == [{"role": "user", "content": "go"}]
    schema = sent["output_config"]["format"]["schema"]
    assert sent["output_config"]["format"]["type"] == "json_schema" and schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "budget_tokens" not in json.dumps(sent) and "tool_choice" not in sent


def test_a_declined_request_is_recorded_as_a_refusal_with_its_reason(world) -> None:  # noqa: ANN001
    gateway, client = frontier_gateway(world.controller, message(stop_reason="refusal", category="cyber",
                                                                 explanation="request resembles malware development"))
    result = gateway.generate(model_config_id="frontier", slot_id="next_step",
                              messages=[Message(role="user", content="analyze this firmware")],
                              output_model=None)
    assert result.status == "refusal"
    assert result.errors == ["the model declined (category: cyber): request resembles malware development"]
    [call] = [c for c in world.store.model_calls() if c["status"] == "refusal"]
    assert call["model_config_id"] == "frontier"
    assert len(client.requests) == 3          # probe x2 + the one call: no automatic rewording and retry


def test_truncated_and_secret_bearing_requests_are_handled_like_local_ones(world) -> None:  # noqa: ANN001
    gateway, client = frontier_gateway(world.controller, message('{"x": 1', stop_reason="max_tokens"),
                                       message("fine"))
    truncated = gateway.generate(model_config_id="frontier", slot_id="s",
                                 messages=[Message(role="user", content="q")], output_model=None)
    assert truncated.status == "truncated"
    gateway.generate(model_config_id="frontier", slot_id="s", output_model=None,
                     messages=[Message(role="user", content="key: AKIAABCDEFGHIJKLMNOP and password=hunter2hunter2")])
    outgoing = json.dumps(client.requests[-1])
    assert "AKIAABCDEFGHIJKLMNOP" not in outgoing          # secrets are redacted before anything leaves


def test_strict_schema_closes_every_object() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}},
              "required": []}
    closed = strict_schema(schema)
    assert closed["additionalProperties"] is False and closed["required"] == ["a"]
    assert closed["properties"]["a"]["additionalProperties"] is False and closed["properties"]["a"]["required"] == ["b"]
    assert schema["required"] == []                       # the input is not modified


# --------------------------------------------------------------- escalation


def stuck_report() -> AssistReport:
    return AssistReport(status="budget_exhausted", turns=10, candidates=["s1", "s2"],
                        detail="candidate budget used; latest failures kept",
                        observations=["tool 'x' is not available",
                                      "patch rejected: patch does not apply exactly to reader.py at line 4",
                                      "case truncated_payload: stdout JSON differs at $.error",
                                      "the previous reply was rejected: malformed_json",
                                      "the previous reply was rejected: context_infeasible"])


def test_the_gap_is_explained_from_the_record() -> None:
    reasons = explain_gap(stuck_report())
    assert reasons[0].startswith("the attempt budget ran out after 10 turns and 2 candidate(s)")
    assert any("check failed: case truncated_payload" in r for r in reasons)
    assert any("2 replies were unusable (context_infeasible, malformed_json)" in r for r in reasons)
    assert any("did not fit the local model's context window" in r for r in reasons)


def test_only_the_engineer_sends_exactly_the_package_they_approved(rig, model_actor) -> None:  # noqa: ANN001
    controller = rig["controller"]
    gateway, client = frontier_gateway(controller, message("Use the record length before slicing."))
    escalation = EscalationService(controller, gateway)
    package = escalation.prepare(
        objective="reject truncated payloads", engineer_context="Packet reader for my bench tooling.",
        report=stuck_report(), files=files_from_snapshot(controller, rig["base"], ["reader.py"]),
        question="What is wrong with the length check?")
    review = render_for_review(package)
    assert package.digest() in review and "Packet reader for my bench tooling." in review
    assert "print(json.dumps(packets))" in review and "the attempt budget ran out" in review
    with pytest.raises(ForgeError, match="only the engineer"):
        escalation.send(package, approved_digest=package.digest(), actor=model_actor, model_config_id="frontier")
    with pytest.raises(ForgeError, match="different package"):
        escalation.send(package, approved_digest="sha256:" + "0" * 64, actor=rig["human"], model_config_id="frontier")
    assert len(client.requests) == 2                      # nothing was sent by the refused attempts
    outcome = escalation.send(package, approved_digest=package.digest(), actor=rig["human"], model_config_id="frontier")
    assert outcome["status"] == "valid" and "record length" in outcome["output"]["text"]
    assert client.requests[-1]["messages"][0]["content"] == package.render()


def test_a_refused_escalation_tells_the_engineer_why_and_what_they_can_do(rig) -> None:  # noqa: ANN001
    controller = rig["controller"]
    gateway, _ = frontier_gateway(controller, message(stop_reason="refusal", category="general_harms",
                                                      explanation="unclear purpose"))
    escalation = EscalationService(controller, gateway)
    package = escalation.prepare(objective="o", engineer_context="c", report=stuck_report(), files=[], question="q")
    outcome = escalation.send(package, approved_digest=package.digest(), actor=rig["human"], model_config_id="frontier")
    assert outcome["status"] == "refusal" and "unclear purpose" in outcome["errors"][0]
    assert "another model you choose: none configured" in outcome["next"]


@needs_sandbox
def test_a_frontier_patch_is_verified_by_the_same_evaluator(rig) -> None:  # noqa: ANN001
    assistant = _assistant(rig)
    good = AgentProposal.model_validate({"action": "propose_patch", "rationale": "frontier fix",
                                         "patch": diff("reader.py", BUGGY, CORRECT), "affected_paths": ["reader.py"]})
    wrong = AgentProposal.model_validate({"action": "propose_patch", "rationale": "confident but wrong",
                                          "patch": diff("reader.py", BUGGY, BUGGY.replace("2", "3", 1)),
                                          "affected_paths": ["reader.py"]})
    rejected = assistant.try_proposal(wrong, _task(rig), rig["base"], rig["criterion_id"])
    assert rejected.status == "rejected" and rejected.detail
    accepted = assistant.try_proposal(good, _task(rig), rig["base"], rig["criterion_id"])
    assert accepted.status == "ready_for_review" and accepted.proposal_id


def test_a_stuck_local_loop_reports_its_gap(rig) -> None:  # noqa: ANN001
    look = {"action": "observe", "rationale": "x", "tool_id": "nonexistent"}
    report = _assistant(rig, {"action": "observe", "rationale": "l", "tool_id": "list_files"}, look, look, look).run(
        _task(rig), rig["base"], rig["criterion_id"], "fix it")
    assert report.status == "no_progress" and report.gap and "repeated an action" in report.gap[0]


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="set ANTHROPIC_API_KEY to run the live check")
def test_live_frontier_call(world) -> None:  # noqa: ANN001
    gateway = ModelGateway(world.controller, adapters={"anthropic": AnthropicAdapter(effort="low")})
    gateway.register(ModelConfig(model_config_id="live", provider_type="anthropic", model_name=DEFAULT_FRONTIER_MODEL,
                                 runtime_version="api", context_capacity=1_000_000, max_output_tokens=4096))
    report = gateway.probe("live")
    assert report.capabilities["structured_output"] == "observed"
