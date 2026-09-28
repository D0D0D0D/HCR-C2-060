"""Inner workflow nodes for HCR-C2-060."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import ClassVar, Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from langgraph.types import interrupt
from shared.utils.audit_logger import emit_trace_event
from src.security.phi import mask_phi, phi_findings
from src.security.text_guards import sanitize_identifier
from src.services.store import knowledge_base, payload_store


def _join_reasons(*values: str) -> str:
    parts: list[str] = []
    for value in values:
        for part in (value or "").split("+"):
            if part and part not in parts:
                parts.append(part)
    return "+".join(parts)


class InputEnvelopeNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("workflow_envelope_decoded", {}, state)
        envelope = json.loads(state.get("user_input") or "{}")
        return {
            "mode": envelope.get("mode"),
            "company_id": envelope.get("company_id"),
            "product_id": envelope.get("product_id"),
            "section_id": envelope.get("section_id"),
            "payload_ref": envelope.get("payload_ref"),
            "tenant_context": envelope.get("tenant_context", {}),
            "section_spec": envelope.get("section_spec", {}),
            "status": AgentStatus.SUCCESS.value,
        }


class ParseDocumentsNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        if state.get("mode") != "ingest":
            return state
        try:
            payload = payload_store.get(state.get("payload_ref", ""))
        except Exception:
            state["status"] = AgentStatus.ERROR.value
            state["validation_errors"] = [{"code": "payload_ref_not_found"}]
            return state
        if phi_findings(payload):
            state["status"] = AgentStatus.ERROR.value
            state["validation_errors"] = [{"code": "phi_detected"}]
        return state

    def execute(self, state: dict) -> dict:
        emit_trace_event("documents_parsed", {"mode": state.get("mode")}, state)
        payload = payload_store.get(state["payload_ref"])
        fmt = payload.get("format", "structured_json") if isinstance(payload, dict) else "structured_json"
        if fmt != "structured_json":
            return {
                "status": AgentStatus.SUCCESS.value,
                "validation_errors": [{"code": "no_applicable_parser", "format": fmt}],
                "ingest_records": [],
            }
        records = payload.get("records", []) if isinstance(payload, dict) else []
        return {"ingest_records": records, "validation_errors": [], "status": AgentStatus.SUCCESS.value}


class ValidateRecordsNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("records_validated", {}, state)
        valid = []
        errors = list(state.get("validation_errors", []))
        for idx, record in enumerate(state.get("ingest_records", [])):
            if phi_findings(record):
                errors.append({"code": "phi_detected", "row": idx})
                continue
            kind = record.get("kind")
            if "inquiry_category" in record and kind is None:
                kind = "inquiry_qa"
            if "body" in record and kind is None:
                kind = "submission_section"
            required = ["company_id", "product_id", "section_id"]
            required.append("body" if kind == "submission_section" else "inquiry_category")
            missing = [key for key in required if not record.get(key)]
            if kind not in {"submission_section", "inquiry_qa"}:
                errors.append({"code": "unsupported_record_kind", "row": idx})
            elif missing:
                errors.append({"code": "missing_required_fields", "row": idx, "fields": missing})
            else:
                valid.append(record)
        return {"ingest_records": valid, "validation_errors": errors, "status": AgentStatus.SUCCESS.value}


class WriteRecordsNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("records_written", {}, state)
        sections = inquiries = 0
        for record in state.get("ingest_records", []):
            enriched = {**record, "company_id": state["company_id"], "product_id": state["product_id"]}
            if record.get("kind") == "submission_section":
                knowledge_base.add_submission_section(enriched)
                sections += 1
            elif record.get("kind") == "inquiry_qa":
                knowledge_base.add_inquiry(enriched)
                inquiries += 1
        return {
            "ingest_summary": {
                "written_submission_sections": sections,
                "written_inquiry_qa": inquiries,
                "validation_errors": state.get("validation_errors", []),
            },
            "status": AgentStatus.SUCCESS.value,
        }


class StructureClaimsNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("claims_structured", {}, state)
        payload = payload_store.get(state["payload_ref"])
        claims = payload.get("claims", []) if isinstance(payload, dict) else []
        claim_sources = []
        for idx, claim in enumerate(claims, start=1):
            source_type = claim.get("source_type", "csr_summary")
            element_id = claim.get("element_id", "")
            base = sanitize_identifier(claim.get("source_id") or f"{source_type}_{element_id}_{idx}")
            digest = sha256(json.dumps(claim, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
            claim_sources.append(
                {
                    "source_id": f"{base}_{digest}",
                    "source_type": source_type,
                    "section_ref": claim.get("section_ref", state["section_id"]),
                    "element_id": element_id,
                    "content_digest": digest,
                    "value": claim.get("value", ""),
                }
            )
        reason = "" if claim_sources else "no_claim_sources"
        return {"claim_sources": claim_sources, "degradation_reason": reason, "status": AgentStatus.SUCCESS.value}


class RetrieveKBNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("kb_retrieved", {"company_id_present": bool(state.get("company_id"))}, state)
        categories = state.get("section_spec", {}).get("frequent_inquiry_categories", [])
        context = knowledge_base.retrieve(state.get("company_id", ""), state.get("product_id", ""), state["section_id"], categories)
        reason = "" if context.get("past_inquiries") else "no_inquiry_history"
        return {"retrieved_context": context, "degradation_reason": _join_reasons(state.get("degradation_reason", ""), reason), "status": AgentStatus.SUCCESS.value}


class GenerateDraftNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: Any = None) -> None:
        self._llm = llm

    def execute(self, state: dict) -> dict:
        emit_trace_event("draft_generated_fallback" if self._llm is None else "draft_generated", {}, state)
        claims = state.get("claim_sources", [])
        skeleton = state.get("section_spec", {}).get("skeleton", [])
        lines = [state.get("section_spec", {}).get("title_ja", "臨床的有効性の概要")]
        lines.extend(str(item) for item in skeleton[:3])
        for claim in claims:
            lines.append(f"{claim.get('value')} [{claim.get('source_id')}]")
        same_product_bodies = [s.get("body", "") for s in state.get("retrieved_context", {}).get("past_sections", []) if s.get("is_same_product") and s.get("body")]
        lines.extend(same_product_bodies[:1])
        cited = [c["source_id"] for c in claims]
        draft = {"section_id": state["section_id"], "body": "\n".join(lines), "cited_source_ids": cited}
        reason = _join_reasons(state.get("degradation_reason", ""), "llm_unavailable" if self._llm is None else "")
        return {"draft_output": draft, "degradation_reason": reason, "status": AgentStatus.SUCCESS.value}


class BuildInquiryArtifactsNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("inquiries_anticipated", {}, state)
        inquiries = [
            {
                "category": item.get("category"),
                "question": item.get("question") or f"{item.get('category')} の論点構造を確認してください。",
                "basis_inquiry_ids": [item["inquiry_id"]],
            }
            for item in state.get("retrieved_context", {}).get("past_inquiries", [])[:3]
        ]
        evidence_map = [{"claim": str(c.get("value", ""))[:120], "source_id": c["source_id"]} for c in state.get("claim_sources", [])]
        present = {c.get("element_id") for c in state.get("claim_sources", [])}
        missing = [
            {"required_element": e.get("id"), "reason": "no_corresponding_claim"}
            for e in state.get("section_spec", {}).get("required_elements", [])
            if e.get("id") not in present
        ]
        return {
            "anticipated_inquiries": inquiries,
            "evidence_map": evidence_map,
            "missing_data_flags": missing,
            "status": AgentStatus.SUCCESS.value,
        }


class HitlRefineNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("hitl_refine_round", {}, state)
        draft = {
            "draft_output": state.get("draft_output", {}),
            "anticipated_inquiries": state.get("anticipated_inquiries", []),
        }
        if state.get("hitl_allowed", True):
            feedback = interrupt({"draft": draft, "prompt": "confirm or refine"})
            clean_feedback, count = mask_phi(feedback)
            if count:
                emit_trace_event("hitl_response_sanitized", {"count": count}, state)
            action = _normalize_action(clean_feedback)
            updated = dict(state.get("draft_output", {}))
            if action == "refine":
                updated["body"] = f"{updated.get('body', '')}\n[HITL refined]"
            return {
                "draft_output": updated,
                "hitl_draft": draft,
                "hitl_feedback": clean_feedback,
                "refine_action": action,
                "status": AgentStatus.SUCCESS.value,
            }
        return {"hitl_draft": draft, "refine_action": "confirm", "status": AgentStatus.SUCCESS.value}


class ResolveInquiryNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        if state.get("mode") != "feedback":
            return state
        try:
            payload = payload_store.get(state.get("payload_ref", ""))
        except Exception:
            state["status"] = AgentStatus.ERROR.value
            state["validation_errors"] = [{"code": "payload_ref_not_found"}]
            return state
        if phi_findings(payload):
            state["status"] = AgentStatus.ERROR.value
            state["validation_errors"] = [{"code": "phi_detected"}]
        return state

    def execute(self, state: dict) -> dict:
        emit_trace_event("inquiry_resolved", {}, state)
        payload = payload_store.get(state["payload_ref"])
        inquiry_id = payload.get("inquiry_id") or f"inq_{sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]}"
        return {"feedback_payload": payload, "resolved_inquiry_id": inquiry_id, "status": AgentStatus.SUCCESS.value}


class UpdateInquiryQANode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("feedback_recorded", {}, state)
        payload = state.get("feedback_payload", {})
        record = knowledge_base.add_inquiry(
            {
                "inquiry_id": state["resolved_inquiry_id"],
                "company_id": state["company_id"],
                "product_id": state["product_id"],
                "section_id": state["section_id"],
                "inquiry_category": payload.get("inquiry_category", "OTHER"),
                "issue_structure": payload.get("issue_structure", "feedback_issue"),
                "inquiry_text": payload.get("inquiry_text", ""),
                "response_text": payload.get("response_text", ""),
                "predicted_by_agent": bool(payload.get("predicted_by_agent", False)),
            }
        )
        return {"updated_inquiry": {"inquiry_id": record["inquiry_id"]}, "status": AgentStatus.SUCCESS.value}


class RecordAdoptedTextNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        emit_trace_event("adopted_text_recorded", {}, state)
        payload = state.get("feedback_payload", {})
        if payload.get("adopted_text"):
            knowledge_base.add_submission_section(
                {
                    "company_id": state["company_id"],
                    "product_id": state["product_id"],
                    "section_id": state["section_id"],
                    "body": payload["adopted_text"],
                    "operator_edited": True,
                }
            )
        return {
            "feedback_result": {
                "inquiry_id": state.get("resolved_inquiry_id"),
                "updated": True,
                "adopted_text_recorded": bool(payload.get("adopted_text")),
            },
            "status": AgentStatus.SUCCESS.value,
        }


def _normalize_action(feedback: Any) -> str:
    if isinstance(feedback, dict):
        value = str(feedback.get("action") or feedback.get("status") or "").casefold()
    else:
        value = str(feedback).casefold()
    if any(word in value for word in ("refine", "correct", "修正")):
        return "refine"
    return "confirm"
