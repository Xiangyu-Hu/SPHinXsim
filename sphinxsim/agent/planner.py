"""Internal natural-language planner for SPHinXsim AgentCAE tasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .intent import PlanningIntent, RuleBasedIntentResolver, TaskIntentType
from .models import AgentPlan, AgentTask, PlanAction, PlanStep, create_task
from .store import AgentTaskStore


class IntentResolver(Protocol):
    """Extension point for rule-based or future LLM intent resolvers."""

    def resolve(self, user_prompt: str) -> PlanningIntent:
        ...


@dataclass(frozen=True)
class PlannedTask:
    """Result of planning; no simulation command has been executed."""

    task: AgentTask
    plan: AgentPlan
    intent: PlanningIntent


class TaskPlanner:
    """Create and persist an internal plan from an English user request."""

    def __init__(
        self,
        store: AgentTaskStore,
        *,
        intent_resolver: IntentResolver | None = None,
    ) -> None:
        self.store = store
        self.intent_resolver = intent_resolver or RuleBasedIntentResolver()

    def create_plan(
        self,
        user_prompt: str,
        *,
        task_id: str | None = None,
    ) -> PlannedTask:
        """Create and persist Task + Plan v1 without printing or executing it."""
        task = create_task(user_prompt, task_id=task_id)
        intent = self.intent_resolver.resolve(task.user_prompt)
        plan = self._build_initial_plan(task, intent)
        self.store.create_task(task)
        persisted_task = self.store.add_plan(task.task_id, plan)
        return PlannedTask(task=persisted_task, plan=plan, intent=intent)

    def _build_initial_plan(
        self,
        task: AgentTask,
        intent: PlanningIntent,
    ) -> AgentPlan:
        steps: list[PlanStep] = []
        plan_root = Path(".build-temp") / "agent_tasks" / task.task_id
        generated_config = plan_root / "cases" / "case_v1.json"

        if intent.intent_type == TaskIntentType.CLARIFY:
            steps.append(
                PlanStep(
                    step_id="clarify_requirements",
                    action=PlanAction.ASK_USER,
                    description="Request the missing information required to plan the simulation.",
                    parameters={"questions": intent.clarification_questions},
                    requires_approval=True,
                )
            )
            return self._plan(task, steps)

        if intent.intent_type == TaskIntentType.UPDATE_CASE:
            assert intent.existing_config_path is not None
            case_step = PlanStep(
                step_id="update_case",
                action=PlanAction.UPDATE_CASE,
                description="Update the existing JSON configuration from the user request.",
                parameters={
                    "config_path": intent.existing_config_path,
                    "instruction": task.user_prompt,
                    "output_path": str(generated_config),
                },
            )
        else:
            case_step = PlanStep(
                step_id="generate_case",
                action=PlanAction.GENERATE_CASE,
                description="Generate and automatically validate a JSON configuration.",
                parameters={
                    "description": task.user_prompt,
                    "output_path": str(generated_config),
                },
            )
        steps.append(case_step)
        previous_step = case_step.step_id

        if intent.should_run:
            run_step = PlanStep(
                step_id="run_case",
                action=PlanAction.RUN_CASE,
                description="Run the complete SPHinXsim simulation from the prepared JSON configuration.",
                parameters={"config_path": str(generated_config)},
                depends_on=[previous_step],
            )
            steps.append(run_step)
            previous_step = run_step.step_id

        if intent.should_analyze:
            analyze_step = PlanStep(
                step_id="analyze_result",
                action=PlanAction.ANALYZE_RESULT,
                description="Analyze the requested simulation results.",
                parameters={"metrics": intent.requested_metrics},
                depends_on=[previous_step],
            )
            steps.append(analyze_step)
            previous_step = analyze_step.step_id

        if intent.should_report:
            steps.append(
                PlanStep(
                    step_id="generate_report",
                    action=PlanAction.GENERATE_REPORT,
                    description="Generate a report from the simulation results.",
                    depends_on=[previous_step],
                )
            )

        return self._plan(task, steps)

    @staticmethod
    def _plan(task: AgentTask, steps: list[PlanStep]) -> AgentPlan:
        return AgentPlan(
            plan_id=f"{task.task_id}_plan_v1",
            version=1,
            objective=task.objective,
            acceptance_criteria=[
                "Every executed SPHinXsim command completes successfully.",
                "Every generated or updated JSON configuration passes schema validation.",
            ],
            steps=steps,
        )
