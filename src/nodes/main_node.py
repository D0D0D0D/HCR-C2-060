"""Outer main GraphNode for the CTD domain workflow."""

from __future__ import annotations

import json
from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from langgraph.checkpoint.memory import MemorySaver
from shared.utils.audit_logger import emit_trace_event
from src.security.text_guards import sanitize_identifier

_PROVENANCE_KEYS = ("claim_sources", "retrieved_context")


class CTDWorkflowGraphNode(GraphNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._subgraph = None

    def get_subgraph(self):
        if self._subgraph is None:
            from src.graph.domain_workflow_graph import CTDWorkflowGraph

            graph = CTDWorkflowGraph(config=self._config)
            if self._config.get("memory_enabled") or self._config.get("hitl", {}).get("enabled", False):
                graph.compile(checkpointer=MemorySaver())
            else:
                graph.compile()
            self._subgraph = graph
        return self._subgraph

    def extract_input(self, state: dict) -> str:
        if not state.get("validated_input"):
            raise SecurityViolationError("S-2: validated_input missing; raw input fallback is forbidden")
        envelope = json.loads(state["validated_input"])
        return json.dumps(envelope, ensure_ascii=False, default=str, sort_keys=True)

    def merge_output(self, state: dict, sub_result: dict) -> dict:
        mode = sub_result.get("mode")
        status = sub_result.get("status", AgentStatus.ERROR.value)
        if mode == "ingest":
            result = {"gate_kind": "ingest", "ingest_summary": sub_result.get("ingest_summary", {}), "status": status}
        elif mode == "feedback":
            result = {
                "gate_kind": "feedback",
                "feedback_result": sub_result.get("feedback_result", {}),
                "status": status,
            }
        elif mode == "invoke":
            result = {
                "gate_kind": "invoke_provenance",
                "draft_output": sub_result.get("draft_output", {}),
                "anticipated_inquiries": sub_result.get("anticipated_inquiries", []),
                "evidence_map": sub_result.get("evidence_map", []),
                "missing_data_flags": sub_result.get("missing_data_flags", []),
                "claim_sources": sub_result.get("claim_sources", []),
                "retrieved_context": sub_result.get("retrieved_context", {}),
                "status": status,
            }
            for key in ("hitl_draft", "hitl_feedback", "hitl_status", "refine_action"):
                if key in sub_result:
                    result[key] = sub_result[key]
        else:
            raise SecurityViolationError(f"S-3: unknown subgraph mode: {mode!r}")
        if sub_result.get("degradation_reason"):
            result["degradation_reason"] = sub_result["degradation_reason"]
        if sub_result.get("validation_errors"):
            result["validation_errors"] = sub_result["validation_errors"]
        if sub_result.get("error_log"):
            result["error_log"] = sub_result["error_log"]
        return result

    def _extra_security_gate_output(self, result: dict) -> dict:
        gate_kind = result.get("gate_kind")
        if gate_kind is None:
            raise SecurityViolationError("S-3: gate_kind not declared by merge_output()")
        if gate_kind != "invoke_provenance":
            return result
        required = [*_PROVENANCE_KEYS, "draft_output", "evidence_map", "anticipated_inquiries"]
        missing = [key for key in required if key not in result]
        if missing:
            raise SecurityViolationError(f"S-3: required field missing on invoke path: {','.join(missing)}")
        known_sources = {sanitize_identifier(c.get("source_id", "")) for c in result["claim_sources"]}
        known_inquiries = {
            sanitize_identifier(q.get("inquiry_id", ""))
            for q in result["retrieved_context"].get("past_inquiries", [])
        }
        cited = {sanitize_identifier(x) for x in result["draft_output"].get("cited_source_ids", [])}
        cited |= {sanitize_identifier(e.get("source_id", "")) for e in result["evidence_map"]}
        inquiry_ids = {
            sanitize_identifier(x)
            for item in result["anticipated_inquiries"]
            for x in item.get("basis_inquiry_ids", [])
        }
        unknown_sources = sorted(x for x in cited - known_sources if x)
        unknown_inquiries = sorted(x for x in inquiry_ids - known_inquiries if x)
        if unknown_sources or unknown_inquiries:
            emit_trace_event(
                "provenance_violation",
                {"unknown_source_ids": unknown_sources, "unknown_inquiry_ids": unknown_inquiries},
                result,
            )
            raise SecurityViolationError("S-3: LLM cited source/inquiry IDs that do not exist")
        emit_trace_event("provenance_verified", {"cited": len(cited), "inquiries": len(inquiry_ids)}, result)
        return {key: value for key, value in result.items() if key not in _PROVENANCE_KEYS}


MainNode = CTDWorkflowGraphNode
