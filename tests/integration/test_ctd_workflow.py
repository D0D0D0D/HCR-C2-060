from __future__ import annotations

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from langgraph.checkpoint.memory import MemorySaver

from src.graph.graph import CTDClinicalSummaryAgent
from src.services.store import knowledge_base, payload_store


def _agent():
    agent = CTDClinicalSummaryAgent(config={"max_retry": 3, "memory_enabled": True, "hitl": {"enabled": True, "max_hitl": 12}})
    agent.compile(checkpointer=MemorySaver())
    return agent


def _ctx(thread_id: str, hitl_allowed: bool = True):
    return InvocationContext(
        session_id=f"s-{thread_id}",
        thread_id=thread_id,
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        hitl_allowed=hitl_allowed,
    )


def test_invoke_hitl_resume_success_and_output_shape():
    knowledge_base.reset()
    payload_store.clear()
    agent = _agent()
    req = {
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload": {"claims": [{"element_id": "study_populations", "value": "FASは100例であった"}]},
    }
    r1 = agent.invoke(json.dumps(req, ensure_ascii=False), ctx=_ctx("invoke-hitl"))
    assert r1["status"] == AgentStatus.AWAITING_HUMAN.value
    r2 = agent.resume(r1["thread_id"], "confirm")
    assert r2["status"] == AgentStatus.SUCCESS.value
    assert r2["output"]["draft"]["cited_source_ids"]
    assert r2["output"]["evidence_map"]
    assert r2["output"]["missing_data_flags"]
    assert "no_inquiry_history" in r2["output"]["degradation_reason"]
    assert "llm_unavailable" in r2["output"]["degradation_reason"]


def test_ingest_structured_json_only_and_feedback_loop_hit():
    knowledge_base.reset()
    payload_store.clear()
    agent = _agent()
    ingest = {
        "mode": "ingest",
        "company_id": "c1",
        "product_id": "p0",
        "section_id": "2.7.3",
        "payload": {
            "format": "structured_json",
            "records": [
                {
                    "kind": "inquiry_qa",
                    "company_id": "ignored",
                    "product_id": "ignored",
                    "section_id": "2.7.3",
                    "inquiry_category": "STAT",
                    "issue_structure": "missing_sensitivity_analysis",
                }
            ],
        },
    }
    out = agent.invoke(json.dumps(ingest, ensure_ascii=False), ctx=_ctx("ingest", hitl_allowed=False))
    assert out["status"] == AgentStatus.SUCCESS.value
    assert out["output"]["ingest_summary"]["written_inquiry_qa"] == 1

    unsupported = {**ingest, "payload": {"format": "pdf", "records": []}}
    out2 = agent.invoke(json.dumps(unsupported, ensure_ascii=False), ctx=_ctx("ingest-pdf", hitl_allowed=False))
    assert out2["output"]["ingest_summary"]["validation_errors"][0]["code"] == "no_applicable_parser"

    feedback = {
        "mode": "feedback",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload": {
            "inquiry_category": "EFFICACY_BASIS",
            "issue_structure": "endpoint_interpretation",
            "adopted_text": "採用文体サンプル。",
            "predicted_by_agent": True,
        },
    }
    fb = agent.invoke(json.dumps(feedback, ensure_ascii=False), ctx=_ctx("feedback", hitl_allowed=False))
    assert fb["output"]["feedback_result"]["adopted_text_recorded"] is True

    invoke = {
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload": {"claims": [{"element_id": "study_populations", "value": "FASは120例であった"}]},
    }
    hit = agent.invoke(json.dumps(invoke, ensure_ascii=False), ctx=_ctx("invoke-no-hitl", hitl_allowed=False))
    assert hit["status"] == AgentStatus.SUCCESS.value
    assert hit["output"]["anticipated_inquiries"]


def test_cross_product_body_is_not_reused_in_draft():
    knowledge_base.reset()
    payload_store.clear()
    knowledge_base.add_submission_section(
        {
            "company_id": "c1",
            "product_id": "old-product",
            "section_id": "2.7.3",
            "body": "旧薬剤X 50mg 試験OLD-001 の本文を流用してはならない。",
        }
    )
    agent = _agent()
    req = {
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "new-product",
        "section_id": "2.7.3",
        "payload": {"claims": [{"element_id": "study_populations", "value": "FASは80例であった"}]},
    }
    out = agent.invoke(json.dumps(req, ensure_ascii=False), ctx=_ctx("cross-product", hitl_allowed=False))
    body = out["output"]["draft"]["body"]
    assert "旧薬剤X" not in body
    assert "50mg" not in body
    assert "OLD-001" not in body
