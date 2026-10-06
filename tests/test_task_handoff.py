"""Regression tests for ML-001/ML-002: a completed task's output must reach
its dependents' input_resource_ids, and the dependent's worker must actually
receive that resource's content (not "No specific input resources provided").
"""

from uuid import uuid4

import pytest

from manager_agent_gym.core.execution.engine import WorkflowExecutionEngine
from manager_agent_gym.core.manager_agent.interface import ManagerAgent
from manager_agent_gym.core.workflow_agents.interface import AgentInterface
from manager_agent_gym.core.workflow_agents.registry import AgentRegistry
from manager_agent_gym.core.workflow_agents.stakeholder_agent import StakeholderAgent
from manager_agent_gym.schemas.config import OutputConfig
from manager_agent_gym.schemas.core.resources import Resource
from manager_agent_gym.schemas.core.tasks import Task
from manager_agent_gym.schemas.core.workflow import Workflow
from manager_agent_gym.schemas.execution.manager import ManagerObservation
from manager_agent_gym.schemas.execution.manager_actions import (
    AssignTaskAction,
    NoOpAction,
)
from manager_agent_gym.schemas.preferences.preference import PreferenceWeights
from manager_agent_gym.schemas.unified_results import create_task_result
from manager_agent_gym.schemas.workflow_agents import AgentConfig
from manager_agent_gym.schemas.workflow_agents.stakeholder import StakeholderConfig

UPSTREAM_CONTENT = "Deliverable from task A: the real finding worth handing off."


class _HandoffAgent(AgentInterface):
    """Produces one resource for "A", and records what it was given for "B"."""

    def __init__(self) -> None:
        super().__init__(
            AgentConfig(
                agent_id="worker",
                agent_type="ai",
                system_prompt="stub worker agent",
                model_name="none",
                agent_description="worker",
                agent_capabilities=["worker"],
            )
        )
        self.resources_seen_by_task_name: dict[str, list[Resource]] = {}

    async def execute_task(self, task: Task, resources: list[Resource]):
        self.resources_seen_by_task_name[task.name] = resources
        output = (
            [Resource(name="A output", description="d", content=UPSTREAM_CONTENT)]
            if task.name == "A"
            else []
        )
        return create_task_result(
            task_id=task.id,
            agent_id=self.agent_id,
            success=True,
            execution_time=0.001,
            resources=output,
            cost=0.0,
        )


class _AssignReadyToWorker(ManagerAgent):
    """Assigns whatever is ready to the single worker agent; otherwise no-ops."""

    def __init__(self) -> None:
        super().__init__(agent_id="manager", preferences=PreferenceWeights(preferences=[]))

    async def step(
        self,
        workflow,
        execution_state,
        stakeholder_profile,
        current_timestep,
        running_tasks,
        completed_task_ids,
        failed_task_ids,
        communication_service=None,
        previous_reward: float = 0.0,
        done: bool = False,
    ):
        observation: ManagerObservation = await self.create_observation(
            workflow=workflow,
            execution_state=execution_state,
            current_timestep=current_timestep,
            running_tasks=running_tasks,
            completed_task_ids=completed_task_ids,
            failed_task_ids=failed_task_ids,
            communication_service=communication_service,
            stakeholder_profile=stakeholder_profile,
        )
        if observation.ready_task_ids:
            return AssignTaskAction(
                reasoning="assign",
                task_id=str(observation.ready_task_ids[0]),
                agent_id="worker",
                success=True,
                result_summary="assign",
            )
        return NoOpAction(reasoning="idle", success=True, result_summary="idle")

    def reset(self) -> None:
        pass


def _build_workflow() -> tuple[Workflow, Task, Task]:
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    a = Task(name="A", description="produce a deliverable")
    b = Task(name="B", description="consume A's deliverable", dependency_task_ids=[a.id])
    w.add_task(a)
    w.add_task(b)
    return w, a, b


def _build_engine(w: Workflow, worker: _HandoffAgent, tmp_path) -> WorkflowExecutionEngine:
    w.add_agent(worker)
    stakeholder = StakeholderAgent(
        config=StakeholderConfig(
            agent_id="stakeholder",
            agent_type="stakeholder",
            system_prompt="Stakeholder",
            model_name="o3",
            name="Stakeholder",
            role="Owner",
            initial_preferences=PreferenceWeights(preferences=[]),
            agent_description="Stakeholder",
            agent_capabilities=["Stakeholder"],
        )
    )
    return WorkflowExecutionEngine(
        workflow=w,
        agent_registry=AgentRegistry(),
        manager_agent=_AssignReadyToWorker(),
        stakeholder_agent=stakeholder,
        output_config=OutputConfig(base_output_dir=tmp_path, create_run_subdirectory=False),
        enable_timestep_logging=False,
        enable_final_metrics_logging=False,
        seed=42,
        max_timesteps=10,
    )


@pytest.mark.asyncio
async def test_dependent_task_receives_predecessor_output_resource_id(tmp_path) -> None:
    w, a, b = _build_workflow()
    worker = _HandoffAgent()
    engine = _build_engine(w, worker, tmp_path)

    await engine.run_full_execution()

    assert w.tasks[a.id].status.value == "completed"
    assert w.tasks[b.id].status.value == "completed"
    a_output_ids = set(w.tasks[a.id].output_resource_ids)
    assert a_output_ids, "task A produced no output to hand off"
    assert a_output_ids <= set(w.tasks[b.id].input_resource_ids), (
        "B's input_resource_ids does not contain A's output"
    )


@pytest.mark.asyncio
async def test_dependent_workers_prompt_actually_receives_the_content(tmp_path) -> None:
    w, a, b = _build_workflow()
    worker = _HandoffAgent()
    engine = _build_engine(w, worker, tmp_path)

    await engine.run_full_execution()

    seen = worker.resources_seen_by_task_name.get("B", [])
    assert seen, "worker never received any input resources for B"
    assert any(r.content == UPSTREAM_CONTENT for r in seen), (
        "B's worker did not receive A's actual output content"
    )


@pytest.mark.asyncio
async def test_independent_task_is_not_given_unrelated_resources(tmp_path) -> None:
    """A task with no dependency on A must not receive A's output."""
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    a = Task(name="A", description="produce a deliverable")
    c = Task(name="C", description="unrelated task")  # no dependency on A
    w.add_task(a)
    w.add_task(c)
    worker = _HandoffAgent()
    engine = _build_engine(w, worker, tmp_path)

    await engine.run_full_execution()

    assert w.tasks[c.id].input_resource_ids == []


@pytest.mark.asyncio
async def test_adequate_handoff_rate_across_executions(tmp_path) -> None:
    """Mirrors the audit's own measurement: % of executions with >=1 populated
    input_resource_id. Three tasks: A (root), B and D both depend on A.
    """
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    a = Task(name="A", description="produce a deliverable")
    b = Task(name="B", description="consume A", dependency_task_ids=[a.id])
    d = Task(name="D", description="also consume A", dependency_task_ids=[a.id])
    w.add_task(a)
    w.add_task(b)
    w.add_task(d)
    worker = _HandoffAgent()
    engine = _build_engine(w, worker, tmp_path)

    await engine.run_full_execution()

    tasks = [w.tasks[a.id], w.tasks[b.id], w.tasks[d.id]]
    with_input = sum(1 for t in tasks if t.input_resource_ids)
    assert with_input / len(tasks) == pytest.approx(2 / 3)
