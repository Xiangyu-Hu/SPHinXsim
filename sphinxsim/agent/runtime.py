"""Persisted runtime settings and factories for the AgentCAE CLI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sphinxsim.services import SPHinXsimCLIAdapter

from .capabilities import build_capability_registry
from .calibration_workflow import MockColumnCollapseCalibrationPlanner
from .knowledge import build_default_knowledge_base
from .planner_context import PlannerContextBuilder
from .replanning import (
    MockReplanningClient,
    NvidiaReplanningClient,
    StructuredReplanner,
)
from .service import AgentCAEAgentService, AgentServiceError
from .store import AgentTaskStore
from .structured_planner import (
    NvidiaStructuredPlannerClient,
    StructuredCalibrationPlanner,
)
from .tools import build_builtin_tool_registry


class AgentRuntimeSettings(BaseModel):
    """Settings required to reconstruct a workflow in a later process."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$")
    planner_provider: Literal["mock", "nvidia"] = "mock"
    replanner_provider: Literal["none", "mock", "nvidia"] = "mock"
    generation_provider: Literal["mock", "nvidia"] = "mock"
    require_approval: bool = True
    command_timeout: float = Field(default=300.0, gt=0.0)


class AgentRuntime:
    """Own one workspace's store, SQLite database, and runtime manifests."""

    def __init__(self, project_root: str | Path, workspace: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        workspace_path = Path(workspace)
        if not workspace_path.is_absolute():
            workspace_path = self.project_root / workspace_path
        self.workspace = workspace_path.resolve()
        self.store = AgentTaskStore(self.workspace / "store")
        self.database_path = self.workspace / "checkpoints.sqlite"

    def save_settings(self, settings: AgentRuntimeSettings) -> Path:
        path = self._settings_path(settings.workflow_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        AgentTaskStore._atomic_write(path, settings.model_dump_json(indent=2))
        return path

    def load_settings(self, workflow_id: str) -> AgentRuntimeSettings:
        path = self._settings_path(workflow_id)
        try:
            return AgentRuntimeSettings.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise AgentServiceError(
                f"runtime settings not found for workflow: {workflow_id}"
            ) from exc

    def create_service(
        self,
        settings: AgentRuntimeSettings,
        *,
        starting: bool,
        execution_enabled: bool = True,
    ) -> AgentCAEAgentService:
        tools = build_builtin_tool_registry()
        if starting and settings.planner_provider == "nvidia":
            capabilities = build_capability_registry(tools)
            context = PlannerContextBuilder(
                tools,
                capabilities,
                build_default_knowledge_base(
                    self.project_root,
                    tools,
                    capabilities,
                ),
            )
            planner = StructuredCalibrationPlanner(
                context,
                NvidiaStructuredPlannerClient.from_environment(),
                tools,
            )
        else:
            planner = MockColumnCollapseCalibrationPlanner()

        replanner = None
        if execution_enabled and settings.replanner_provider == "mock":
            replanner = StructuredReplanner(
                MockReplanningClient(
                    [
                        {
                            "action": "retry_candidate",
                            "reason": "Retry a transient deterministic tool failure.",
                        }
                    ]
                )
            )
        elif execution_enabled and settings.replanner_provider == "nvidia":
            replanner = StructuredReplanner(
                NvidiaReplanningClient.from_environment()
            )

        environment = {
            "SPHINXSIM_LLM_PROVIDER": settings.generation_provider,
        }
        if settings.generation_provider == "nvidia":
            key = os.getenv("NVIDIA_NIM_API_KEY", os.getenv("NVIDIA_API_KEY"))
            if not key:
                raise AgentServiceError(
                    "NVIDIA generation requires NVIDIA_NIM_API_KEY or NVIDIA_API_KEY"
                )
        adapter = SPHinXsimCLIAdapter(
            cwd=self.project_root,
            environment=environment,
            default_timeout=settings.command_timeout,
        )
        return AgentCAEAgentService(
            self.store,
            self.database_path,
            planner,
            adapter,
            command_timeout=settings.command_timeout,
            require_approval=settings.require_approval,
            replanner=replanner,
        )

    def _settings_path(self, workflow_id: str) -> Path:
        if not workflow_id or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in workflow_id
        ):
            raise AgentServiceError(
                "workflow_id may contain only letters, digits, '_' and '-'"
            )
        return self.workspace / "runtime" / f"{workflow_id}.json"
