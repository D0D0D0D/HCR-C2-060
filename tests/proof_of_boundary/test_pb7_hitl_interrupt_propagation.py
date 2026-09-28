from __future__ import annotations

import json
import pathlib
import warnings

import pytest
import yaml
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from langgraph.checkpoint.memory import MemorySaver

from src.graph.domain_workflow_graph import CTDWorkflowGraph
from src.nodes.workflow_nodes import HitlRefineNode

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true.

    An absent or unreadable config.yaml warns instead of skipping silently —
    either case is ambiguous (never shipped / broken
    vs. genuinely non-HITL) and should not look like a clean auto-waiver.
    """
    if not _CONFIG_PATH.exists():
        warnings.warn(
            f"{_CONFIG_PATH} not found — PB-7 skipped without verifying hitl.enabled. "
            "If this template calls interrupt(), ship config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    try:
        import yaml  # pyyaml==6.0.1 — pinned in scaffold/pyproject.toml dependencies

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(
            f"{_CONFIG_PATH} could not be read as YAML ({exc}) — PB-7 skipped without "
            "verifying hitl.enabled. If this template calls interrupt(), fix config/config.yaml "
            "before release.",
            stacklevel=2,
        )
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(
            f"{_CONFIG_PATH} does not have the expected 'hitl:' mapping shape — PB-7 skipped "
            "without verifying hitl.enabled. If this template calls interrupt(), fix "
            "config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)


def test_hitl_enabled_manifest_is_fail_closed():
    data = yaml.safe_load(_CONFIG_PATH.read_text())
    assert data["hitl"]["enabled"] is True


def _state(hitl_allowed=True):
    return {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "pb7",
        "session_id": "pb7-session",
        "thread_id": "pb7-thread",
        "trace_id": "pb7-trace",
        "node_history": [],
        "error_log": [],
        "execution_time": {},
        "hitl_allowed": hitl_allowed,
        "draft_output": {"section_id": "2.7.3", "body": "draft", "cited_source_ids": []},
        "anticipated_inquiries": [],
    }


def test_pb7_hitl_interrupt_propagates():
    graph = CTDWorkflowGraph(config={"memory_enabled": True, "hitl": {"enabled": True, "max_hitl": 12}})
    graph.compile(checkpointer=MemorySaver())
    envelope = {
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload_ref": _payload_ref(),
        "tenant_context": {"company_id": "c1", "isolation_verified": True, "product_id": "p1"},
        "section_spec": {
            "section_id": "2.7.3",
            "title_ja": "臨床的有効性の概要",
            "required_elements": [{"id": "study_populations"}],
            "frequent_inquiry_categories": [],
            "skeleton": ["2.7.3.1 臨床的有効性の背景"],
        },
    }
    ctx = InvocationContext(
        session_id="pb7-session-a",
        thread_id="pb7-propagates",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
    )
    first = graph.invoke(json.dumps(envelope, ensure_ascii=False), ctx=ctx)
    assert first["status"] == AgentStatus.AWAITING_HUMAN.value
    assert "error" not in first.get("status", "")


def test_pb7_hitl_allowed_false_skips_interrupt():
    node = HitlRefineNode()
    result = node(_state(hitl_allowed=False))
    assert result["status"] == AgentStatus.SUCCESS.value
    assert result["refine_action"] == "confirm"


def test_pb7_resume_reaches_success():
    graph = CTDWorkflowGraph(config={"memory_enabled": True, "hitl": {"enabled": True, "max_hitl": 12}})
    graph.compile(checkpointer=MemorySaver())
    envelope = {
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload_ref": _payload_ref(),
        "tenant_context": {"company_id": "c1", "isolation_verified": True, "product_id": "p1"},
        "section_spec": {
            "section_id": "2.7.3",
            "title_ja": "臨床的有効性の概要",
            "required_elements": [{"id": "study_populations"}],
            "frequent_inquiry_categories": [],
            "skeleton": ["2.7.3.1 臨床的有効性の背景"],
        },
    }
    ctx = InvocationContext(
        session_id="pb7-session",
        thread_id="pb7-resume",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
    )
    first = graph.invoke(json.dumps(envelope, ensure_ascii=False), ctx=ctx)
    assert first["status"] == AgentStatus.AWAITING_HUMAN.value
    second = graph.resume(first["thread_id"], "confirm")
    assert second["status"] == AgentStatus.SUCCESS.value


def _payload_ref() -> str:
    from src.services.store import payload_store

    return payload_store.put({"claims": [{"element_id": "study_populations", "value": "FAS 100例"}]})
