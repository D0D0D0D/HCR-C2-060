from __future__ import annotations

import ast
import inspect
import json
import pkgutil
import importlib
from pathlib import Path

import pytest
import yaml

from framework.errors import SecurityViolationError
from framework.nodes.base_node import BaseNode
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.main_node import CTDWorkflowGraphNode
from src.nodes.post_process_node import CTDPostProcessNode
from src.nodes.pre_process_node import CTDPreProcessNode
from src.nodes.workflow_nodes import StructureClaimsNode, RetrieveKBNode
from src.security.phi import mask_phi, phi_findings
from src.services.store import knowledge_base, payload_store


def _state(**extra):
    base = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "unit",
        "session_id": "unit-session",
        "thread_id": "unit-thread",
        "trace_id": "unit-trace",
        "node_history": [],
        "error_log": [],
        "execution_time": {},
    }
    base.update(extra)
    return base


def test_tc01_state_contract_excludes_sensitive_inner_fields():
    from src.schemas.state import State

    annotations = State.__annotations__
    assert "claim_sources" not in annotations
    assert "retrieved_context" not in annotations
    assert not any("InvocationContext" in str(v) for v in annotations.values())


def test_tc02_s2_rejects_phi_on_ingest_and_masks_on_invoke():
    node = CTDPreProcessNode()
    ingest = {
        "mode": "ingest",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload": {"format": "structured_json", "records": [{"body": "患者 田中太郎 が参加"}]},
    }
    rejected = node(_state(user_input=json.dumps(ingest, ensure_ascii=False)))
    assert rejected["status"] == AgentStatus.ERROR.value
    assert rejected["validation_errors"][0]["code"] == "phi_detected"

    invoke = {
        "mode": "invoke",
        "company_id": "c1",
        "product_id": "p1",
        "section_id": "2.7.3",
        "payload": {"claims": [{"element_id": "study_populations", "value": "症例番号 ABC123 は除外"}]},
    }
    accepted = node(_state(user_input=json.dumps(invoke, ensure_ascii=False)))
    assert accepted["status"] == AgentStatus.SUCCESS.value
    assert accepted["degradation_reason"] == "phi_masked_in_input"
    stored = payload_store.get(accepted["payload_ref"])
    assert "[PHI_MASKED]" in stored["claims"][0]["value"]


def test_tc03_src_contains_no_credential_literals():
    for _, modname, _ in pkgutil.walk_packages(["src"], prefix="src."):
        if ".examples." in modname:
            continue
        module = importlib.import_module(modname)
        path = getattr(module, "__file__", "")
        if path and path.endswith(".py"):
            text = open(path).read()
            assert "mock-jwt-for-testing" not in text
            assert "sk-" not in text


def test_tc04_nodes_do_not_construct_invocation_context_directly():
    offenders = []
    for _, modname, _ in pkgutil.walk_packages(["src/nodes"], prefix="src.nodes."):
        module = importlib.import_module(modname)
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "InvocationContext":
                offenders.append(f"{modname}:{node.lineno}")
    assert offenders == []


def test_tc05_no_lifecycle_audit_events_in_execute():
    forbidden = {"node_start", "node_complete", "node_error"}
    for _, modname, _ in pkgutil.walk_packages(["src/nodes"], prefix="src.nodes."):
        module = importlib.import_module(modname)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseNode) and obj is not BaseNode:
                source = inspect.getsource(obj.execute)
                assert not any(term in source for term in forbidden)


def test_tc06_tc07_function_nodes_only_use_extra_security_hooks():
    for _, modname, _ in pkgutil.walk_packages(["src/nodes"], prefix="src.nodes."):
        module = importlib.import_module(modname)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, FunctionNode) and obj is not FunctionNode:
                assert "_security_gate_input" not in obj.__dict__
                assert "_security_gate_output" not in obj.__dict__


def test_tc08_all_nodes_declare_and_enforce_trust_level():
    node_classes = []
    for _, modname, _ in pkgutil.walk_packages(["src/nodes"], prefix="src.nodes."):
        module = importlib.import_module(modname)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseNode) and obj is not BaseNode and obj.__module__ == modname:
                node_classes.append(obj)
    assert node_classes
    for cls in node_classes:
        assert "required_trust_level" in cls.__dict__, cls.__name__
        assert cls.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
        denied = cls()(_state(caller_trust_level=TrustLevel.ANONYMOUS.value))
        assert denied["status"] == AgentStatus.ERROR.value


def test_tc10_graphnode_provenance_pass_block_and_fail_closed():
    node = CTDWorkflowGraphNode()
    good = {
        "gate_kind": "invoke_provenance",
        "claim_sources": [{"source_id": "SRC_1"}],
        "retrieved_context": {"past_inquiries": [{"inquiry_id": "INQ_1"}]},
        "draft_output": {"cited_source_ids": ["SRC_1"]},
        "evidence_map": [{"source_id": "SRC_1", "claim": "x"}],
        "anticipated_inquiries": [{"basis_inquiry_ids": ["INQ_1"]}],
    }
    cleaned = node._extra_security_gate_output(good)
    assert "claim_sources" not in cleaned
    assert "retrieved_context" not in cleaned

    bad = {**good, "draft_output": {"cited_source_ids": ["SRC_999"]}}
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_output(bad)
    missing = {k: v for k, v in good.items() if k != "claim_sources"}
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_output(missing)
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_output({"draft_output": {}})


def test_tc10_postprocess_assertion_guard_and_phi_rescan():
    node = CTDPostProcessNode()
    result = {
        "formatted_output": {
            "draft": {"body": "本剤は有効である。患者 田中太郎 が改善した。", "cited_source_ids": []}
        },
        "status": AgentStatus.SUCCESS.value,
    }
    guarded = node._extra_security_gate_output(result)
    assert "有効である" not in guarded["formatted_output"]["draft"]["body"]
    assert "[PHI_MASKED]" in guarded["formatted_output"]["draft"]["body"]


def test_f1_assertion_guard_blocks_non_body_free_text_channels():
    node = CTDPostProcessNode()
    question_result = {
        "formatted_output": {
            "draft": {"body": "根拠に基づき記載する。", "cited_source_ids": []},
            "anticipated_inquiries": [{"question": "本剤は有効であることが示唆されます"}],
            "evidence_map": [],
            "missing_data_flags": [],
        },
        "status": AgentStatus.SUCCESS.value,
    }
    question_blocked = node._extra_security_gate_output(question_result)
    assert question_blocked["status"] == AgentStatus.ERROR.value

    claim_result = {
        "formatted_output": {
            "draft": {"body": "根拠に基づき記載する。", "cited_source_ids": []},
            "anticipated_inquiries": [],
            "evidence_map": [{"claim": "本剤は安全である"}],
            "missing_data_flags": [],
        },
        "status": AgentStatus.SUCCESS.value,
    }
    claim_blocked = node._extra_security_gate_output(claim_result)
    assert claim_blocked["status"] == AgentStatus.ERROR.value


def test_sec08_extract_input_requires_validated_input():
    with pytest.raises(SecurityViolationError):
        CTDWorkflowGraphNode().extract_input({"user_input": "{}"})


def test_phi_sanitizer_blocks_bypass_and_preserves_domain_terms():
    blocked_inputs = [
        "患者Ａ.Ｂ. は症例番号 ABC123 として登録",
        "職長の熊埜御堂茂が骨折",
        "作業員タナカタロウ",
        "被験者ID: SUBJ-001 は死亡",
        "田中太郎が来院",
    ]
    for text in blocked_inputs:
        assert phi_findings(text), text
    clinical = {
        "records": [
            {
                "inquiry_text": "職長の熊埜御堂茂が投与後に肝機能障害",
                "response_text": "被験者タナカタロウは回復",
            }
        ]
    }
    assert phi_findings(clinical)

    masked, count = mask_phi("被験者 田中太郎 が来院。投与後に肝機能障害を認め死亡。")
    assert count >= 1
    assert "[PHI_MASKED]" in masked
    assert "投与後" in masked
    assert "肝機能障害" in masked
    assert "死亡" in masked
    clean = "主要評価項目とFAS解析対象集団を記載する。投与後に肝機能障害を認め死亡。"
    assert not phi_findings(clean, include_sdk=False)


def test_f3_config_split_runtime_keys_live_in_config_yaml():
    manifest = yaml.safe_load(Path("config/agent.yaml").read_text())
    runtime = yaml.safe_load(Path("config/config.yaml").read_text())
    # Top-level hitl in agent.yaml was the 2026-07-14 convention, superseded by the agent.yaml/config.yaml split.
    assert "hitl" not in manifest
    assert "agent" not in manifest
    assert "config" not in manifest
    assert runtime["hitl"]["enabled"] is True
    for key in ("max_retry", "timeout_seconds", "memory_enabled", "hitl", "ingest", "retrieval"):
        assert key in runtime


def test_stage1_deterministic_ids_and_stage2_tenant_scope():
    knowledge_base.reset()
    payload_store.clear()
    ref = payload_store.put({"claims": [{"element_id": "study_populations", "value": "FAS 100例"}]})
    state = _state(payload_ref=ref, section_id="2.7.3")
    first = StructureClaimsNode().execute(state)["claim_sources"][0]["source_id"]
    second = StructureClaimsNode().execute(state)["claim_sources"][0]["source_id"]
    assert first == second
    assert all(ch.isalnum() or ch in "_-" for ch in first)

    knowledge_base.add_inquiry(
        {"company_id": "c1", "product_id": "other", "section_id": "2.7.3", "inquiry_category": "STAT"}
    )
    knowledge_base.add_inquiry(
        {"company_id": "c2", "product_id": "p1", "section_id": "2.7.3", "inquiry_category": "STAT"}
    )
    out = RetrieveKBNode().execute(
        _state(company_id="c1", product_id="p1", section_id="2.7.3", section_spec={"frequent_inquiry_categories": ["STAT"]})
    )
    assert len(out["retrieved_context"]["past_inquiries"]) == 1
    assert out["retrieved_context"]["past_inquiries"][0]["product_id"] == "other"
