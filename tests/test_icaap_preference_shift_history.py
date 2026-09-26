"""NEW-001 end to end: replay ICAAP's scripted preference schedule.

run_examples applies create_preference_update_requests() to the stakeholder up
front, so the timeline is built by successive apply_weight_update calls exactly
as in a real run. Before the fix, each update rewrote the earlier entry it was
based on, so early timesteps saw later weights.
"""

import pytest

from examples.end_to_end_examples.icap.preferences import (
    create_preference_update_requests,
    create_preferences,
)
from manager_agent_gym.core.workflow_agents.stakeholder_agent import StakeholderAgent
from manager_agent_gym.schemas.workflow_agents.stakeholder import StakeholderConfig


@pytest.fixture
def stakeholder() -> StakeholderAgent:
    cfg = StakeholderConfig(
        agent_id="stakeholder",
        agent_type="stakeholder",
        system_prompt="Stakeholder",
        model_name="o3",
        name="Stakeholder",
        role="Owner",
        initial_preferences=create_preferences(),
        agent_description="Stakeholder",
        agent_capabilities=["Stakeholder"],
    )
    return StakeholderAgent(config=cfg)


def test_every_timestep_sees_the_weights_scheduled_for_it(
    stakeholder: StakeholderAgent,
) -> None:
    requests = sorted(create_preference_update_requests(), key=lambda r: r.timestep)
    assert len(requests) > 1, "ICAAP is expected to script several shifts"

    stakeholder.apply_weight_updates(requests)

    def expected_at(t: int) -> dict[str, float]:
        active = [r for r in requests if r.timestep <= t][-1]
        total = sum(active.changes.values())
        return {k: v / total for k, v in active.changes.items()}

    horizon = requests[-1].timestep + 5
    for t in range(horizon):
        got = stakeholder.get_preferences_for_timestep(t).get_preference_dict()
        want = expected_at(t)
        assert got.keys() == want.keys()
        for name in want:
            assert got[name] == pytest.approx(want[name]), f"t={t} {name}"


def _scenarios_with_scripted_shifts() -> list[str]:
    from examples.scenarios import SCENARIOS

    return sorted(
        name for name, spec in SCENARIOS.items() if spec.create_preference_update_requests
    )


@pytest.mark.parametrize("scenario", _scenarios_with_scripted_shifts())
def test_no_scenario_update_rewrites_earlier_timesteps(scenario: str) -> None:
    """An update at timestep T must never change what timesteps before T see."""
    from examples.scenarios import SCENARIOS

    spec = SCENARIOS[scenario]
    cfg = StakeholderConfig(
        agent_id="stakeholder",
        agent_type="stakeholder",
        system_prompt="Stakeholder",
        model_name="o3",
        name="Stakeholder",
        role="Owner",
        initial_preferences=spec.create_preferences(),
        agent_description="Stakeholder",
        agent_capabilities=["Stakeholder"],
    )
    agent = StakeholderAgent(config=cfg)

    for request in sorted(spec.create_preference_update_requests(), key=lambda r: r.timestep):
        before = {
            t: agent.get_preferences_for_timestep(t).get_preference_dict()
            for t in range(request.timestep)
        }
        agent.apply_weight_update(request)
        for t, weights in before.items():
            assert agent.get_preferences_for_timestep(t).get_preference_dict() == weights, (
                f"{scenario}: update at t={request.timestep} rewrote t={t}"
            )
