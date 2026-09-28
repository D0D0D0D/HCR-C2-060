"""Outer post-process node: S-3 assertion/PHI guards and output shaping."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.security.phi import mask_phi
from src.security.text_guards import guard_assertions


class CTDPostProcessNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("output_format_started", {"mode": state.get("mode")}, state)
        if state.get("status") == AgentStatus.ERROR.value:
            return {"status": AgentStatus.ERROR.value, "formatted_output": {"errors": state.get("error_log", [])}}
        mode = state.get("mode")
        if mode == "invoke":
            formatted = {
                "section_id": state.get("section_id"),
                "draft": state.get("draft_output", {}),
                "anticipated_inquiries": state.get("anticipated_inquiries", []),
                "evidence_map": state.get("evidence_map", []),
                "missing_data_flags": state.get("missing_data_flags", []),
            }
        elif mode == "ingest":
            formatted = {"ingest_summary": state.get("ingest_summary", {})}
        elif mode == "feedback":
            formatted = {"feedback_result": state.get("feedback_result", {})}
        else:
            return {"status": AgentStatus.ERROR.value, "error_log": [f"unknown mode in post_process: {mode!r}"]}
        if state.get("degradation_reason"):
            formatted["degradation_reason"] = state["degradation_reason"]
        return {"formatted_output": formatted, "status": AgentStatus.SUCCESS.value}

    def _extra_security_gate_output(self, result: dict) -> dict:
        output = result.get("formatted_output")
        if not isinstance(output, dict):
            return result
        output, blocked, matched_count = _guard_free_text_fields(output, _load_forbidden_terms())
        if matched_count:
            emit_trace_event("assertion_guard_applied", {"count": matched_count}, result)
        if blocked:
            result["status"] = AgentStatus.ERROR.value
            result["error_log"] = ["S-3: assertion guard blocked unsupported assertion text"]
            return result
        masked, count = mask_phi(output, include_sdk=False)
        if count:
            emit_trace_event("phi_masked_in_output", {"count": count}, result)
        result["formatted_output"] = masked
        return result


def _load_forbidden_terms() -> list[str]:
    path = Path("config") / "assertion_lexicon.yaml"
    if not path.exists():
        return ["有効である", "安全である", "優越性が示された"]
    data = yaml.safe_load(path.read_text()) or {}
    return list(data.get("forbidden_terms", []))


_FREE_TEXT_PATHS = (
    ("draft", "body"),
    ("anticipated_inquiries", "*", "question"),
    ("evidence_map", "*", "claim"),
    ("missing_data_flags", "*", "reason"),
)


def _guard_free_text_fields(output: dict, terms: list[str]) -> tuple[dict, bool, int]:
    guarded_output = dict(output)
    blocked = False
    matched_count = 0
    for path in _FREE_TEXT_PATHS:
        guarded_output, path_blocked, path_count = _guard_path(guarded_output, path, terms)
        blocked = blocked or path_blocked
        matched_count += path_count
    return guarded_output, blocked, matched_count


def _guard_path(value, path: tuple[str, ...], terms: list[str]):
    if not path:
        if not isinstance(value, str):
            return value, False, 0
        guarded, blocked, matched = guard_assertions(value, terms)
        return guarded, blocked, len(matched)
    head, *tail = path
    if head == "*":
        if not isinstance(value, list):
            return value, False, 0
        blocked = False
        matched_count = 0
        items = []
        for item in value:
            guarded, item_blocked, item_count = _guard_path(item, tuple(tail), terms)
            items.append(guarded)
            blocked = blocked or item_blocked
            matched_count += item_count
        return items, blocked, matched_count
    if not isinstance(value, dict) or head not in value:
        return value, False, 0
    guarded_child, blocked, matched_count = _guard_path(value[head], tuple(tail), terms)
    return {**value, head: guarded_child}, blocked, matched_count


PostProcessNode = CTDPostProcessNode
