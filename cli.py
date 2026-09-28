"""AGENTIC STAR Marketplace entry adapter for HCR-C2-060."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import yaml
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

try:
    from framework.utils.config_loader import load_agent_config
except ImportError:  # AgentCore 1.0.0 / 1.0.1 compatibility
    from framework.utils.config_loader import load_config

    def load_agent_config(agent_dir: Path) -> dict[str, Any]:
        config_path = agent_dir / "config" / "config.yaml"
        return load_config(str(config_path)) if config_path.exists() else {}


try:
    from shared.bootstrap import run_agent_marketplace
except ModuleNotFoundError as exc:  # pragma: no cover - Marketplace extra is absent locally
    if exc.name != "shared.bootstrap":
        raise
    _BOOTSTRAP_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def run_agent_marketplace(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError(
            "The installed AgentCore wheel does not provide Marketplace bootstrap support"
        ) from _BOOTSTRAP_IMPORT_ERROR
else:
    _BOOTSTRAP_IMPORT_ERROR = None


_REPO_ROOT = Path(__file__).resolve().parent
os.chdir(_REPO_ROOT)

from src.graph.graph import CTDClinicalSummaryAgent  # noqa: E402

_NAMESPACE = "hcr"
_AGENT_NAME = "HCR-C2-060"
_PROVIDER_AGENT_NAME = "HCR-C2-060"

_SECTION_SPEC_PATH = _REPO_ROOT / "config" / "section_specs" / "2.7.3.yaml"
_ASSERTION_LEXICON_PATH = _REPO_ROOT / "config" / "assertion_lexicon.yaml"


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Required Marketplace {label} is missing: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Required Marketplace {label} is not readable YAML: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Required Marketplace {label} must contain a YAML mapping: {path}")
    return cast(dict[str, Any], value)


def _validate_bundled_config() -> None:
    _load_yaml_mapping(_SECTION_SPEC_PATH, "section specification")
    lexicon = _load_yaml_mapping(_ASSERTION_LEXICON_PATH, "assertion lexicon")
    forbidden_terms = lexicon.get("forbidden_terms")
    if not isinstance(forbidden_terms, list) or not forbidden_terms:
        raise RuntimeError(
            "Required Marketplace assertion lexicon must define a non-empty forbidden_terms list: "
            f"{_ASSERTION_LEXICON_PATH}"
        )


def _marketplace_user_input(user_input: str) -> str:
    try:
        payload = json.loads(user_input)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["mode"] = "invoke"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class MarketplaceGraph(CTDClinicalSummaryAgent):
    """Marketplace-only config, read-only mode, context, and HITL boundary."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        _validate_bundled_config()
        merged = dict(config or {})
        super().__init__(config=merged)

    def compile(self, checkpointer: BaseCheckpointSaver | None = None) -> None:
        super().compile(checkpointer=checkpointer or MemorySaver())

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del input_context
        marketplace_ctx = replace(ctx, hitl_allowed=False) if ctx is not None else ctx
        return cast(
            dict[str, Any],
            super().invoke(
                _marketplace_user_input(user_input),
                session_id=session_id,
                ctx=marketplace_ctx,
                input_context={},
            ),
        )

    def _enrich_output(self, output: dict[str, Any], result: dict[str, Any]) -> None:
        super()._enrich_output(output, result)
        if output.get("output") is None and output.get("status") == AgentStatus.ERROR.value:
            output["output"] = {"errors": result.get("validation_errors") or result.get("error_log", [])}


def main() -> None:
    config = load_agent_config(_REPO_ROOT)
    run_agent_marketplace(
        MarketplaceGraph,
        agent_name=_AGENT_NAME,
        namespace=_NAMESPACE,
        config=config,
    )


if __name__ == "__main__":
    main()
