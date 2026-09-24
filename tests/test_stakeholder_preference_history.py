"""Regression tests for NEW-001: weight updates must not rewrite earlier timeline entries."""

import pytest

from manager_agent_gym.core.workflow_agents.stakeholder_agent import StakeholderAgent
from manager_agent_gym.schemas.preferences.preference import (
    Preference,
    PreferenceWeights,
)
from manager_agent_gym.schemas.preferences.weight_update import (
    PreferenceWeightUpdateRequest,
)
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
        initial_preferences=PreferenceWeights(
            preferences=[
                Preference(name="speed", weight=0.5),
                Preference(name="quality", weight=0.5),
            ]
        ),
        agent_description="Stakeholder",
        agent_capabilities=["Stakeholder"],
    )
    return StakeholderAgent(config=cfg)


def _weights_at(agent: StakeholderAgent, timestep: int) -> dict[str, float]:
    return agent.get_preferences_for_timestep(timestep).get_preference_dict()


@pytest.mark.parametrize(
    "mode,changes",
    [
        ("delta", {"speed": -0.3, "quality": 0.3}),
        ("multiplier", {"speed": 0.2, "quality": 3.0}),
        ("absolute", {"speed": 0.1, "quality": 0.9}),
    ],
)
def test_update_leaves_earlier_timeline_entry_untouched(
    stakeholder: StakeholderAgent, mode: str, changes: dict[str, float]
) -> None:
    before = _weights_at(stakeholder, 0)

    stakeholder.apply_weight_update(
        PreferenceWeightUpdateRequest(timestep=7, changes=changes, mode=mode)
    )

    assert _weights_at(stakeholder, 0) == before
    assert _weights_at(stakeholder, 6) == before
    assert _weights_at(stakeholder, 7) != before


def test_chained_updates_preserve_every_earlier_entry(
    stakeholder: StakeholderAgent,
) -> None:
    w0 = _weights_at(stakeholder, 0)

    stakeholder.apply_weight_update(
        PreferenceWeightUpdateRequest(
            timestep=3, changes={"speed": -0.3, "quality": 0.3}
        )
    )
    w3 = _weights_at(stakeholder, 3)

    stakeholder.apply_weight_update(
        PreferenceWeightUpdateRequest(
            timestep=7, changes={"speed": 0.2, "quality": -0.2}
        )
    )

    assert _weights_at(stakeholder, 0) == w0
    assert _weights_at(stakeholder, 3) == w3
    assert w0 != w3 != _weights_at(stakeholder, 7)


def test_change_event_reports_true_previous_weights(
    stakeholder: StakeholderAgent,
) -> None:
    before = _weights_at(stakeholder, 0)

    change = stakeholder.apply_weight_update(
        PreferenceWeightUpdateRequest(
            timestep=7, changes={"speed": -0.3, "quality": 0.3}
        )
    )

    assert change.previous_weights == before
    assert change.new_weights == _weights_at(stakeholder, 7)
