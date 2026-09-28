"""Inner three-mode CTD workflow graph."""

from __future__ import annotations

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.nodes.workflow_nodes import (
    BuildInquiryArtifactsNode,
    GenerateDraftNode,
    HitlRefineNode,
    InputEnvelopeNode,
    ParseDocumentsNode,
    RecordAdoptedTextNode,
    ResolveInquiryNode,
    RetrieveKBNode,
    StructureClaimsNode,
    UpdateInquiryQANode,
    ValidateRecordsNode,
    WriteRecordsNode,
)
from src.schemas.state import CTDWorkflowState


class CTDWorkflowGraph(BaseGraph):
    @property
    def name(self) -> str:
        return "ctd_clinical_summary_workflow"

    @property
    def state_schema(self) -> type:
        return CTDWorkflowState

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        self._nodes = {
            "envelope": InputEnvelopeNode(),
            "parse_documents": ParseDocumentsNode(),
            "validate_records": ValidateRecordsNode(),
            "write_records": WriteRecordsNode(),
            "structure_claims": StructureClaimsNode(),
            "retrieve_kb": RetrieveKBNode(),
            "generate_draft": GenerateDraftNode(llm=self.config.get("llm")),
            "build_artifacts": BuildInquiryArtifactsNode(),
            "hitl_refine": HitlRefineNode(),
            "resolve_inquiry": ResolveInquiryNode(),
            "update_inquiry_qa": UpdateInquiryQANode(),
            "record_adopted_text": RecordAdoptedTextNode(),
        }

    def add_edges(self) -> None:
        self._sg.add_edge(START, "envelope")
        self._sg.add_conditional_edges("envelope", self.route)
        self._sg.add_edge("parse_documents", "validate_records")
        self._sg.add_edge("validate_records", "write_records")
        self._sg.add_edge("write_records", END)
        self._sg.add_edge("structure_claims", "retrieve_kb")
        self._sg.add_edge("retrieve_kb", "generate_draft")
        self._sg.add_edge("generate_draft", "build_artifacts")
        self._sg.add_edge("build_artifacts", "hitl_refine")
        self._sg.add_edge("hitl_refine", END)
        self._sg.add_edge("resolve_inquiry", "update_inquiry_qa")
        self._sg.add_edge("update_inquiry_qa", "record_adopted_text")
        self._sg.add_edge("record_adopted_text", END)

    def route(self, state: CTDWorkflowState) -> str:
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        mode = state.get("mode")
        if mode == "ingest":
            return "parse_documents"
        if mode == "feedback":
            return "resolve_inquiry"
        return "structure_claims"

    def get_output(self, state: CTDWorkflowState) -> dict:
        keys = [
            "mode",
            "status",
            "claim_sources",
            "retrieved_context",
            "draft_output",
            "anticipated_inquiries",
            "evidence_map",
            "missing_data_flags",
            "ingest_summary",
            "feedback_result",
            "degradation_reason",
            "validation_errors",
            "hitl_draft",
            "hitl_feedback",
            "hitl_status",
            "refine_action",
            "trace_id",
            "error_log",
        ]
        return {key: state.get(key) for key in keys if key in state and state.get(key) is not None}
