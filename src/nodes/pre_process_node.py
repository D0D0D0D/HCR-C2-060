"""Outer pre-process node: validation, tenant context, S-2 PHI routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Any

import yaml

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.security.phi import mask_phi, phi_findings
from src.services.store import payload_store

_SUPPORTED_MODES = {"ingest", "invoke", "feedback"}
_SUPPORTED_SECTIONS = {"2.7.3"}
_MAX_INPUT_BYTES = 65536


class CTDPreProcessNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        raw = state.get("user_input", "")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = ["S-2: input must be JSON"]
            return state
        mode = payload.get("mode")
        if mode not in _SUPPORTED_MODES:
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = [f"S-2: unsupported mode: {mode!r}"]
            return state
        if len(json.dumps(payload, ensure_ascii=False, default=str).encode()) > _MAX_INPUT_BYTES:
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = ["S-2: input exceeds max size"]
            return state
        findings = phi_findings(payload.get("payload", payload.get("documents", payload)))
        if findings and mode in {"ingest", "feedback"}:
            state["status"] = AgentStatus.ERROR.value
            state["validation_errors"] = [{"code": "phi_detected", "count": len(findings)}]
            state["error_log"] = ["S-2: PHI detected on persistent write path"]
            return state
        return state

    def execute(self, state: dict) -> dict:
        emit_trace_event("input_validation_started", {}, state)
        payload = json.loads(state.get("user_input") or "{}")
        mode = payload["mode"]
        company_id = str(payload.get("company_id", "")).strip()
        product_id = str(payload.get("product_id", "")).strip()
        section_id = str(payload.get("section_id", "2.7.3")).strip()
        errors = []
        if not company_id:
            errors.append({"code": "company_id_required"})
        if not product_id:
            errors.append({"code": "product_id_required"})
        if section_id not in _SUPPORTED_SECTIONS:
            errors.append({"code": "unsupported_section", "section_id": section_id})
        if errors:
            emit_trace_event("input_validation_failed", {"count": len(errors)}, state)
            return {"status": AgentStatus.ERROR.value, "validation_errors": errors, "error_log": [str(errors)]}

        working_payload: Any = payload.get("payload", {})
        degradation_reason = payload.get("degradation_reason", "")
        if mode == "invoke":
            working_payload, masked_count = mask_phi(working_payload)
            if masked_count:
                emit_trace_event("phi_masked_in_input", {"count": masked_count}, state)
                degradation_reason = _join_reasons(degradation_reason, "phi_masked_in_input")

        client_ref = payload.get("payload_ref")
        if client_ref:
            emit_trace_event("client_payload_ref_discarded", {}, state)
        payload_ref = payload_store.put(working_payload)
        section_spec = _load_section_spec(section_id)
        tenant_context = {"company_id": company_id, "isolation_verified": True, "product_id": product_id}
        validated = {
            "mode": mode,
            "company_id": company_id,
            "product_id": product_id,
            "section_id": section_id,
            "payload_ref": payload_ref,
            "tenant_context": tenant_context,
            "section_spec": section_spec,
        }
        emit_trace_event("tenant_resolved", {"company_id_present": bool(company_id)}, state)
        result = {
            "validated_input": json.dumps(validated, ensure_ascii=False, sort_keys=True),
            "mode": mode,
            "company_id": company_id,
            "product_id": product_id,
            "section_id": section_id,
            "payload_ref": payload_ref,
            "tenant_context": tenant_context,
            "section_spec": section_spec,
            "status": AgentStatus.SUCCESS.value,
        }
        if degradation_reason:
            result["degradation_reason"] = degradation_reason
        return result


def _join_reasons(existing: str, reason: str) -> str:
    parts = [p for p in (existing or "").split("+") if p]
    if reason not in parts:
        parts.append(reason)
    return "+".join(parts)


def _load_section_spec(section_id: str) -> dict:
    path = Path("config") / "section_specs" / f"{section_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"section_spec_not_found:{section_id}")
    return yaml.safe_load(path.read_text()) or {}


PreProcessNode = CTDPreProcessNode
