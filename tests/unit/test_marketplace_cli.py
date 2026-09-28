from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from langgraph.checkpoint.memory import MemorySaver

import cli
from src.graph.graph import CTDClinicalSummaryAgent
from src.services.store import knowledge_base, payload_store


def _config() -> dict[str, Any]:
    return cli.load_agent_config(cli._REPO_ROOT)


def _ctx(name: str = "marketplace") -> InvocationContext:
    return InvocationContext(
        session_id=f"session-{name}",
        thread_id=f"thread-{name}",
        caller_id="marketplace-user",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        hitl_allowed=True,
    )


def _invoke(agent: cli.MarketplaceGraph, user_input: str, *, name: str) -> dict[str, Any]:
    with bound_secrets(agent._secrets_provider):
        return agent.invoke(
            user_input,
            ctx=_ctx(name),
            input_context={"conversation_history": [{"role": "user", "content": "CANARY"}]},
        )


def _valid_request(mode: str = "invoke") -> dict[str, Any]:
    payload: dict[str, Any]
    if mode == "ingest":
        payload = {
            "format": "structured_json",
            "records": [
                {
                    "kind": "inquiry_qa",
                    "company_id": "company-a",
                    "product_id": "product-a",
                    "section_id": "2.7.3",
                    "inquiry_category": "STAT",
                }
            ],
        }
    elif mode == "feedback":
        payload = {"inquiry_category": "STAT", "adopted_text": "operator text"}
    else:
        payload = {"claims": [{"element_id": "study_populations", "value": "FAS 100 cases"}]}
    return {
        "mode": mode,
        "company_id": "company-a",
        "product_id": "product-a",
        "section_id": "2.7.3",
        "payload": payload,
    }


def test_runner_context_is_rebuilt_and_hitl_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def recorder(
        self: CTDClinicalSummaryAgent,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del self
        captured.update(
            user_input=user_input,
            session_id=session_id,
            ctx=ctx,
            input_context=input_context,
        )
        return {"status": AgentStatus.SUCCESS.value, "output": {}}

    monkeypatch.setattr(CTDClinicalSummaryAgent, "invoke", recorder)
    agent = cli.MarketplaceGraph(config=_config())
    result = agent.invoke(
        json.dumps(_valid_request()),
        session_id="explicit-session",
        ctx=_ctx("recorder"),
        input_context={"conversation_history": [{"content": "CANARY"}]},
    )

    assert result["status"] == AgentStatus.SUCCESS.value
    assert captured["input_context"] == {}
    assert "CANARY" not in json.dumps(captured, default=str)
    assert captured["ctx"].hitl_allowed is False


@pytest.mark.parametrize("requested_mode", ["ingest", "feedback"])
def test_marketplace_write_modes_cannot_reach_storage(requested_mode: str) -> None:
    knowledge_base.reset()
    payload_store.clear()
    agent = cli.MarketplaceGraph(config=_config())
    agent.compile()

    result = _invoke(agent, json.dumps(_valid_request(requested_mode)), name=f"mode-{requested_mode}")

    assert result["status"] == AgentStatus.SUCCESS.value
    assert "ingest_summary" not in result["output"]
    assert "feedback_result" not in result["output"]
    assert knowledge_base.submission_sections == []
    assert knowledge_base.inquiry_qa == []


def test_runner_order_invoke_succeeds_without_context_canary() -> None:
    knowledge_base.reset()
    payload_store.clear()
    agent = cli.MarketplaceGraph(config=_config())
    agent.compile()
    agent.provision_secrets(agent._secrets_provider)

    result = _invoke(agent, json.dumps(_valid_request()), name="runner-order")

    assert result["status"] == AgentStatus.SUCCESS.value
    assert result["output"]["draft"]["body"]
    assert "CANARY" not in json.dumps(result, ensure_ascii=False, default=str)
    assert "no_inquiry_history" in result["output"]["degradation_reason"]
    assert "llm_unavailable" in result["output"]["degradation_reason"]


def test_non_json_input_returns_structured_validation_errors() -> None:
    agent = cli.MarketplaceGraph(config=_config())
    agent.compile()

    result = _invoke(agent, "臨床的有効性の概要を作成してください", name="non-json")

    assert result["status"] == AgentStatus.ERROR.value
    assert "company_id_required" in json.dumps(result["output"])
    assert "product_id_required" in json.dumps(result["output"])


@pytest.mark.parametrize("path_name", ["_SECTION_SPEC_PATH", "_ASSERTION_LEXICON_PATH"])
def test_missing_bundled_config_fails_during_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path_name: str
) -> None:
    monkeypatch.setattr(cli, path_name, tmp_path / "missing.yaml")

    with pytest.raises(RuntimeError, match="missing"):
        cli.MarketplaceGraph(config=_config())


def test_invalid_bundled_config_shapes_fail_during_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    section_spec = tmp_path / "section.yaml"
    section_spec.write_text("- not-a-mapping\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_SECTION_SPEC_PATH", section_spec)
    with pytest.raises(RuntimeError, match="mapping"):
        cli.MarketplaceGraph(config=_config())

    lexicon = tmp_path / "lexicon.yaml"
    lexicon.write_text("forbidden_terms: []\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_SECTION_SPEC_PATH", cli._REPO_ROOT / "config/section_specs/2.7.3.yaml")
    monkeypatch.setattr(cli, "_ASSERTION_LEXICON_PATH", lexicon)
    with pytest.raises(RuntimeError, match="non-empty forbidden_terms"):
        cli.MarketplaceGraph(config=_config())


def test_compile_installs_default_and_preserves_explicit_checkpointer() -> None:
    default_agent = cli.MarketplaceGraph(config=_config())
    default_agent.compile()
    assert isinstance(default_agent._compiled.checkpointer, MemorySaver)

    explicit = MemorySaver()
    explicit_agent = cli.MarketplaceGraph(config=_config())
    explicit_agent.compile(checkpointer=explicit)
    assert explicit_agent._compiled.checkpointer is explicit


def test_import_from_another_cwd_restores_repo_relative_config_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    module_name = "marketplace_cli_from_other_cwd"
    spec = importlib.util.spec_from_file_location(module_name, cli.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        assert Path.cwd() == cli._REPO_ROOT
        agent = module.MarketplaceGraph(config=module.load_agent_config(module._REPO_ROOT))
        agent.compile()
        with bound_secrets(agent._secrets_provider):
            result = agent.invoke(json.dumps(_valid_request()), ctx=_ctx("other-cwd"))
        assert result["status"] == AgentStatus.SUCCESS.value
    finally:
        sys.modules.pop(module_name, None)


def test_main_calls_marketplace_runner_with_loaded_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def recorder(graph_class: type, **kwargs: Any) -> None:
        captured.update(graph_class=graph_class, **kwargs)

    monkeypatch.setattr(cli, "run_agent_marketplace", recorder)

    cli.main()

    assert captured["graph_class"] is cli.MarketplaceGraph
    assert captured["agent_name"] == "HCR-C2-060"
    assert captured["namespace"] == "hcr"
    assert captured["config"]
