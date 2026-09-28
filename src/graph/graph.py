"""Outer AgentBaseGraph for HCR-C2-060."""

from __future__ import annotations

from framework.graph.agent_base_graph import AgentBaseGraph
from src.nodes.main_node import CTDWorkflowGraphNode
from src.nodes.post_process_node import CTDPostProcessNode
from src.nodes.pre_process_node import CTDPreProcessNode
from src.schemas.state import State


class CTDClinicalSummaryAgent(AgentBaseGraph):
    @property
    def name(self) -> str:
        return "HCR-C2-060"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()
        self._nodes["pre_process"] = CTDPreProcessNode()
        self._nodes["main"] = CTDWorkflowGraphNode(config=self.config)
        self._nodes["post_process"] = CTDPostProcessNode()


Graph = CTDClinicalSummaryAgent
