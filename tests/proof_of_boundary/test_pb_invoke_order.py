from __future__ import annotations

import importlib
import inspect
import pkgutil

from framework.nodes.base_node import BaseNode
from framework.schemas.trust_level import TrustLevel
from typing import ClassVar


def _discover_node_classes() -> list[type]:
    import src.nodes as pkg

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


def _state_for(node_cls: type) -> dict:
    from src.services.store import payload_store

    ref = payload_store.put({"claims": [{"element_id": "study_populations", "value": "FAS 100例"}]})
    base = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "pb6",
        "session_id": "pb6-session",
        "thread_id": "pb6-thread",
        "trace_id": "pb6-trace",
        "node_history": [],
        "error_log": [],
        "execution_time": {},
        "hitl_allowed": False,
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload_ref": ref,
        "section_spec": {"required_elements": [], "frequent_inquiry_categories": [], "skeleton": []},
        "claim_sources": [{"source_id": "SRC_1", "value": "x", "element_id": "study_populations"}],
        "retrieved_context": {"past_inquiries": [], "past_sections": []},
        "draft_output": {"section_id": "2.7.3", "body": "draft", "cited_source_ids": ["SRC_1"]},
        "anticipated_inquiries": [],
        "evidence_map": [{"source_id": "SRC_1", "claim": "x"}],
        "feedback_payload": {"inquiry_category": "OTHER"},
        "resolved_inquiry_id": "inq_pb6",
        "ingest_records": [],
        "validation_errors": [],
    }
    if node_cls.__name__ == "CTDPreProcessNode":
        base["user_input"] = '{"mode":"invoke","company_id":"c1","product_id":"p1","section_id":"2.7.3","payload":{"claims":[]}}'
    if node_cls.__name__ == "CTDWorkflowGraphNode":
        base["validated_input"] = (
            '{"company_id":"c1","mode":"invoke","payload_ref":"'
            + ref
            + '","product_id":"p1","section_id":"2.7.3","section_spec":{"required_elements":[],"frequent_inquiry_categories":[],"skeleton":[]},"tenant_context":{"company_id":"c1","isolation_verified":true,"product_id":"p1"}}'
        )
    if node_cls.__name__ == "InputEnvelopeNode":
        base["user_input"] = base.get("validated_input", '{"mode":"invoke","company_id":"c1","product_id":"p1","section_id":"2.7.3","payload_ref":"' + ref + '","section_spec":{},"tenant_context":{}}')
    return base


# ── PB-6 S-1 negative boundary（2026-08-17 正本追従で移植）──────────────
# 正本 tests/proof_of_boundary/test_pb_invoke_order.py より。テンプレのノード構成に
# 依存しない常設フィクスチャで「不足 trust では execute() に到達しない」ことを証明する
# （TC-08 の要求。既存の全ノード走査テストは触っていない）。


class _PrivilegedTrustGateFixture(BaseNode):
    """Always-present privileged node used to prove the S-1 negative boundary."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _security_gate_input(self, state):
        return state

    def execute(self, state):
        return {"status": "success"}

    def _security_gate_output(self, result):
        return result


def _trust_predecessor(required: TrustLevel) -> TrustLevel:
    """Return a lower valid trust level; fail loudly if the framework adds one."""
    predecessors = {
        TrustLevel.VERIFIED_EXTERNAL: TrustLevel.ANONYMOUS,
        TrustLevel.INTERNAL: TrustLevel.VERIFIED_EXTERNAL,
    }
    try:
        return predecessors[required]
    except KeyError as exc:
        raise AssertionError(f"no lower trust level defined for {required!r}") from exc


def test_call_order_for_every_node(monkeypatch):
    import framework.nodes.base_node as base_node_module

    failures: list[str] = []
    for node_cls in _discover_node_classes():
        order: list[str] = []
        monkeypatch.setattr(
            base_node_module,
            "emit_trace_event",
            lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
        )
        for method_name, label in (
            ("_security_gate_input", "security_gate_input"),
            ("execute", "execute"),
            ("_security_gate_output", "security_gate_output"),
        ):
            original = getattr(node_cls, method_name)

            def spy(self, arg, _o=order, _label=label, _orig=original):
                _o.append(_label)
                return _orig(self, arg)

            monkeypatch.setattr(node_cls, method_name, spy)
        result = node_cls()(_state_for(node_cls))
        compact = []
        for item in order:
            if item in {"security_gate_input", "execute", "security_gate_output"}:
                compact.append(item)
            elif item == "event:node_start" and "execute" not in compact:
                compact.append(item)
            elif item == "event:node_complete" and "security_gate_output" in compact:
                compact.append(item)
        expected = ["event:node_start", "security_gate_input", "execute", "security_gate_output", "event:node_complete"]
        if compact != expected or result.get("status") == "error":
            failures.append(f"{node_cls.__name__}: expected {expected}, got {compact}, raw={order}, result={result}")
    assert not failures, "\n\n".join(failures)


def test_s1_denial_refuses_execution_before_execute(monkeypatch):
    """TC-08: an always-present privileged node proves the negative S-1 path."""
    import framework.nodes.base_node as base_node_module

    events: list[str] = []
    execute_calls: list[object] = []
    monkeypatch.setattr(
        base_node_module,
        "emit_trace_event",
        lambda event_type, _payload, _state: events.append(event_type),
    )
    original_execute = _PrivilegedTrustGateFixture.execute

    def spy_execute(self, state):
        execute_calls.append(state)
        return original_execute(self, state)

    monkeypatch.setattr(_PrivilegedTrustGateFixture, "execute", spy_execute)
    result = _PrivilegedTrustGateFixture()(
        {
            "caller_trust_level": _trust_predecessor(
                _PrivilegedTrustGateFixture.required_trust_level
            ).value,
            "correlation_id": "tc08-s1-denial",
        }
    )

    assert result["status"] == "error"
    assert "S-1 trust gate denied" in result["error_log"][0]
    assert events == ["s1_denied"]
    assert not execute_calls
