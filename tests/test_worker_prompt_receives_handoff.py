"""Settles the open question from Fix 2's first real-run check: does a real
worker's prompt actually contain a predecessor's content?

This calls the real AIAgent._create_task_prompt directly — the exact method
the engine uses to build a worker's prompt — with no network call (building
an Agent/LitellmModel object is local; only Runner.run() would hit the API,
and this test never calls it). So it's free and deterministic, unlike a full
run where whether a dependent completes within the timestep budget is
down to what the manager happens to pick.

Also documents ML-006 (200-char truncation) precisely: the prompt only ever
contains the first 200 characters of a resource's content, however large the
resource actually is.
"""

from uuid import uuid4

import pytest

from manager_agent_gym.core.workflow_agents.ai_agent import AIAgent
from manager_agent_gym.core.workflow_agents.prompts.ai_agent_prompts import (
    NO_RESOURCES_MESSAGE,
)
from manager_agent_gym.schemas.core.resources import Resource
from manager_agent_gym.schemas.core.tasks import Task
from manager_agent_gym.schemas.workflow_agents import AIAgentConfig

REAL_PREDECESSOR_CONTENT = (
    "Reverse Stress Test Framework: scenario design, severity calibration, "
    "and attribution methodology for the ICAAP normative capital projection."
)
assert len(REAL_PREDECESSOR_CONTENT) <= 200, "keep this short enough to appear in full below"


@pytest.fixture
def worker() -> AIAgent:
    config = AIAgentConfig(
        agent_id="worker",
        agent_type="ai",
        system_prompt="You are a diligent risk analyst.",
        model_name="gpt-4o-mini",
        agent_description="stub worker agent",
        agent_capabilities=["analysis"],
    )
    return AIAgent(config=config, tools=[])


def _dependent_task() -> Task:
    return Task(name="3-Year Capital Planning", description="Use the upstream framework")


def test_worker_prompt_with_no_resources_shows_the_fallback_message(worker: AIAgent) -> None:
    prompt = worker._create_task_prompt(_dependent_task(), resources=[])
    assert NO_RESOURCES_MESSAGE in prompt
    assert REAL_PREDECESSOR_CONTENT not in prompt


def test_worker_prompt_with_a_predecessor_resource_contains_its_real_content(
    worker: AIAgent,
) -> None:
    predecessor_output = Resource(
        name="Reverse Stress Test Framework Example",
        description="Output of the Reverse Stress Test task",
        content=REAL_PREDECESSOR_CONTENT,
    )

    prompt = worker._create_task_prompt(_dependent_task(), resources=[predecessor_output])

    assert NO_RESOURCES_MESSAGE not in prompt
    assert predecessor_output.name in prompt
    # the content is short enough here to appear in full
    assert REAL_PREDECESSOR_CONTENT in prompt


def test_worker_prompt_truncates_long_predecessor_content_at_200_chars(
    worker: AIAgent,
) -> None:
    """Documents ML-006: Fix 2 delivers the real resource, but the prompt the
    worker actually sees is still cut to the first 200 characters.
    """
    long_content = REAL_PREDECESSOR_CONTENT * 5  # well over 200 chars
    assert len(long_content) > 200
    predecessor_output = Resource(
        name="Long Upstream Deliverable",
        description="d",
        content=long_content,
    )

    prompt = worker._create_task_prompt(_dependent_task(), resources=[predecessor_output])

    assert long_content[:200] in prompt
    assert long_content not in prompt  # the full content is NOT in the prompt
    assert long_content[200:] not in prompt
