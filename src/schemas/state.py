"""State schemas for HCR-C2-060."""

from __future__ import annotations

from typing import NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Outer graph state.

    Do not add claim_sources or retrieved_context here. They contain clinical
    values and KB text references and are consumed inside the GraphNode boundary.
    """

    validated_input: str
    mode: str
    company_id: str
    product_id: str
    section_id: NotRequired[str]
    payload_ref: str
    tenant_context: NotRequired[dict]
    section_spec: NotRequired[dict]
    gate_kind: NotRequired[str]
    draft_output: NotRequired[dict]
    anticipated_inquiries: NotRequired[list[dict]]
    evidence_map: NotRequired[list[dict]]
    missing_data_flags: NotRequired[list[dict]]
    ingest_summary: NotRequired[dict]
    feedback_result: NotRequired[dict]
    refine_action: NotRequired[str]
    formatted_output: NotRequired[dict]
    degradation_reason: NotRequired[str]
    validation_errors: NotRequired[list[dict]]


class CTDWorkflowState(AgentState):
    """Inner graph state. Clinical source values stay inside this schema."""

    mode: str
    company_id: str
    product_id: str
    section_id: str
    payload_ref: str
    tenant_context: dict
    section_spec: dict
    claim_sources: NotRequired[list[dict]]
    retrieved_context: NotRequired[dict]
    draft_output: NotRequired[dict]
    anticipated_inquiries: NotRequired[list[dict]]
    evidence_map: NotRequired[list[dict]]
    missing_data_flags: NotRequired[list[dict]]
    ingest_summary: NotRequired[dict]
    feedback_result: NotRequired[dict]
    degradation_reason: NotRequired[str]
    validation_errors: NotRequired[list[dict]]
    ingest_records: NotRequired[list[dict]]
    feedback_payload: NotRequired[dict]
    resolved_inquiry_id: NotRequired[str]
    updated_inquiry: NotRequired[dict]
    refine_action: NotRequired[str]
