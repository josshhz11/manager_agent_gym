"""Regression tests for ML-051/052/053: churn/reassignment integrity.

Three claims from the audit, checked independently before fixing anything:

(a) ML-051 — a task assigned to a worker who then leaves is stranded: its
    assigned_agent_id points at an agent no longer in workflow.agents, and
    nothing ever clears it, so it can never start. Confirmed below; fixed.
(b) ML-052 — AssignTaskAction accepts assignments to composite tasks, tasks
    with unmet dependencies, or tasks already RUNNING/COMPLETED/FAILED, and
    reports success regardless. Confirmed below; fixed.
(c) ML-053 (rejection hidden from the manager) — traced where ActionResult
    reaches the manager's action buffer (on_action_executed uses
    action_result.success, not the action instance's own .success field,
    and nothing in manager_agent/*.py or engine.py reads the latter). Does
    NOT reproduce on the current code — see the dedicated test at the
    bottom. Not fixed; the plan doc's "what's left" section documents why.
"""

from uuid import uuid4

import pytest

from manager_agent_gym.core.execution.engine import WorkflowExecutionEngine
from manager_agent_gym.core.manager_agent.interface import ManagerAgent
from manager_agent_gym.core.workflow_agents.interface import AgentInterface
from manager_agent_gym.core.workflow_agents.registry import AgentRegistry
from manager_agent_gym.core.workflow_agents.stakeholder_agent import StakeholderAgent
from manager_agent_gym.schemas.config import OutputConfig
from manager_agent_gym.schemas.core.base import TaskStatus
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


class _InstantWorker(AgentInterface):
    def __init__(self, agent_id: str) -> None:
        super().__init__(
            AgentConfig(
                agent_id=agent_id,
                agent_type="ai",
                system_prompt="stub worker agent",
                model_name="none",
                agent_description="stub worker agent",
                agent_capabilities=["worker"],
            )
        )

    async def execute_task(self, task: Task, resources: list[Resource]):
        return create_task_result(
            task_id=task.id,
            agent_id=self.agent_id,
            success=True,
            execution_time=0.001,
            resources=[],
            cost=0.0,
        )


class _AssignFirstReadyToAvailableAgent(ManagerAgent):
    """Assigns the first *unassigned* ready task to the first available agent.

    Only touches tasks without a current assignee -- a task already carrying
    an assigned_agent_id (even a stale one pointing at a departed worker) is
    left alone, so any reassignment that happens is attributable to the
    engine's own orphaned-assignment handling, not to this manager just
    blindly reassigning everything every tick.
    """

    def __init__(self) -> None:
        super().__init__(agent_id="manager", preferences=PreferenceWeights(preferences=[]))
        self.rejections_seen: list[str] = []

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
        unassigned_ready = [
            tid for tid in observation.ready_task_ids
            if not workflow.tasks[tid].assigned_agent_id
        ]
        if unassigned_ready and observation.available_agent_metadata:
            return AssignTaskAction(
                reasoning="assign",
                task_id=str(unassigned_ready[0]),
                agent_id=observation.available_agent_metadata[0].agent_id,
                success=True,
                result_summary="assign",
            )
        return NoOpAction(reasoning="idle", success=True, result_summary="idle")

    def on_action_executed(self, timestep, action, action_result) -> None:
        if action_result is not None and not action_result.success:
            self.rejections_seen.append(action_result.summary)
        super().on_action_executed(timestep, action, action_result)

    def reset(self) -> None:
        pass


def _stakeholder() -> StakeholderAgent:
    return StakeholderAgent(
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


def _build_engine(
    w: Workflow, agent_registry: AgentRegistry, manager: ManagerAgent, tmp_path
) -> WorkflowExecutionEngine:
    return WorkflowExecutionEngine(
        workflow=w,
        agent_registry=agent_registry,
        manager_agent=manager,
        stakeholder_agent=_stakeholder(),
        output_config=OutputConfig(base_output_dir=tmp_path, create_run_subdirectory=False),
        enable_timestep_logging=False,
        enable_final_metrics_logging=False,
        seed=42,
        max_timesteps=6,
    )


# ---------------------------------------------------------------------------
# (a) ML-051: a worker leaving strands its already-assigned, not-yet-started task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_assigned_to_a_departed_worker_is_requeued_and_completes(tmp_path) -> None:
    """Reproduces ML-051's actual observable state directly: a task whose
    assigned_agent_id refers to an agent no longer in workflow.agents.

    Driving this end-to-end through AgentRegistry.schedule_agent_remove would
    need a multi-tick dependency chain to land the removal in the exact tick
    between the worker being mirrored in and the task starting (pruning only
    fires for agents that were present at the END of the previous tick, so a
    same-tick add+remove is invisible to it) -- correct, but a lot of brittle
    timing choreography to pin down the one thing actually in question here:
    does the engine ever recover a task stuck on a now-absent agent? Testing
    that state directly is simpler and no less faithful to the real defect.
    """
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    a = Task(name="A", description="d")
    w.add_task(a)

    registry = AgentRegistry()
    backup_worker = _InstantWorker("backup_worker")
    registry.register_agent(backup_worker)
    w.add_agent(backup_worker)

    # "leaving_worker" was assigned this task and then left -- it is not, and
    # never will be, in workflow.agents.
    a.assigned_agent_id = "leaving_worker"
    a.status = TaskStatus.READY

    manager = _AssignFirstReadyToAvailableAgent()
    engine = _build_engine(w, registry, manager, tmp_path)

    await engine.run_full_execution()

    assert w.tasks[a.id].status == TaskStatus.COMPLETED
    assert w.tasks[a.id].assigned_agent_id == "backup_worker"


# ---------------------------------------------------------------------------
# (b) ML-052: AssignTaskAction accepts un-executable assignments as "success"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_to_composite_task_is_rejected() -> None:
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    parent = Task(name="parent", description="d")
    parent.add_subtask(Task(name="child", description="d"))
    w.add_task(parent)
    worker = _InstantWorker("worker")
    w.add_agent(worker)

    action = AssignTaskAction(
        reasoning="x", task_id=str(parent.id), agent_id="worker", success=None, result_summary=None
    )
    result = await action.execute(w)

    assert result.success is False
    assert w.tasks[parent.id].assigned_agent_id is None


@pytest.mark.asyncio
async def test_assign_to_task_with_unmet_dependencies_is_rejected() -> None:
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    blocker = Task(name="blocker", description="d")
    blocked = Task(name="blocked", description="d", dependency_task_ids=[blocker.id])
    w.add_task(blocker)
    w.add_task(blocked)
    worker = _InstantWorker("worker")
    w.add_agent(worker)

    action = AssignTaskAction(
        reasoning="x", task_id=str(blocked.id), agent_id="worker", success=None, result_summary=None
    )
    result = await action.execute(w)

    assert result.success is False
    assert w.tasks[blocked.id].assigned_agent_id is None


@pytest.mark.asyncio
async def test_assign_to_already_completed_task_is_rejected() -> None:
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    done = Task(name="done", description="d", status=TaskStatus.COMPLETED)
    w.add_task(done)
    worker = _InstantWorker("worker")
    w.add_agent(worker)

    action = AssignTaskAction(
        reasoning="x", task_id=str(done.id), agent_id="worker", success=None, result_summary=None
    )
    result = await action.execute(w)

    assert result.success is False


@pytest.mark.asyncio
async def test_assign_to_a_ready_task_still_succeeds() -> None:
    """Make sure the new validation doesn't reject the normal, valid case."""
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    t = Task(name="t", description="d", status=TaskStatus.READY)
    w.add_task(t)
    worker = _InstantWorker("worker")
    w.add_agent(worker)

    action = AssignTaskAction(
        reasoning="x", task_id=str(t.id), agent_id="worker", success=None, result_summary=None
    )
    result = await action.execute(w)

    assert result.success is True
    assert w.tasks[t.id].assigned_agent_id == "worker"


# ---------------------------------------------------------------------------
# (c) ML-053: is a rejection actually hidden from the manager's next step?
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_does_see_rejected_assignments(tmp_path) -> None:
    """Checks the audit's third claim directly, rather than assuming it.

    Uses a rejection path that already exists on unfixed code (assigning to
    an agent_id that doesn't exist) so this is independent of fix (b) above
    -- deliberately not "assign to an already-completed task", since that
    only becomes a rejection once (b) is implemented. A manager that keeps
    making this invalid assignment should see every attempt reported as a
    failure via on_action_executed's action_result -- if ML-053 reproduced,
    this list would stay empty.
    """
    w = Workflow(name="w", workflow_goal="d", owner_id=uuid4())
    t = Task(name="t", description="d")
    w.add_task(t)
    registry = AgentRegistry()
    # deliberately no agents registered: "ghost_worker" will never exist

    class _AlwaysAssignToNonexistentAgent(ManagerAgent):
        def __init__(self) -> None:
            super().__init__(agent_id="m", preferences=PreferenceWeights(preferences=[]))
            self.rejections_seen: list[str] = []

        async def step(self, workflow, execution_state, stakeholder_profile, current_timestep,
                        running_tasks, completed_task_ids, failed_task_ids,
                        communication_service=None, previous_reward=0.0, done=False):
            return AssignTaskAction(
                reasoning="x", task_id=str(t.id), agent_id="ghost_worker",
                success=True, result_summary="assign",
            )

        def on_action_executed(self, timestep, action, action_result) -> None:
            if action_result is not None and not action_result.success:
                self.rejections_seen.append(action_result.summary)
            super().on_action_executed(timestep, action, action_result)

        def reset(self) -> None:
            pass

    manager = _AlwaysAssignToNonexistentAgent()
    engine = _build_engine(w, registry, manager, tmp_path)

    await engine.run_full_execution()

    assert manager.rejections_seen, "manager never saw any rejection via action_result"
    assert len(manager.rejections_seen) == 6  # one per timestep, max_timesteps=6
