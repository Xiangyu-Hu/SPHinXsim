"""Schema tests for velocity boundaries and startup acceleration."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig


def _heat_transfer_payload():
    path = Path(__file__).parent / "test_simulation/test_2d_simulation/data/heat_transfer.json"
    return json.loads(path.read_text())


def test_velocity_and_startup_acceleration_round_trip():
    payload = _heat_transfer_payload()
    config = SimulationConfig.model_validate(payload)
    dumped = config.model_dump(mode="json", exclude_none=True)
    assert dumped["fluid_boundary_conditions"][0]["velocity"] == payload["fluid_boundary_conditions"][0]["velocity"]
    assert dumped["startup_acceleration"] == payload["startup_acceleration"]
    assert SimulationConfig.model_validate(dumped) == config


def test_velocity_relaxation_defaults_to_direct_enforcement():
    payload = _heat_transfer_payload()
    del payload["fluid_boundary_conditions"][0]["velocity"]["relaxation_rate"]
    config = SimulationConfig.model_validate(payload)
    assert config.fluid_boundary_conditions[0].velocity.relaxation_rate == 1.0


@pytest.mark.parametrize("field,value", [
    ("relaxation_rate", -0.1), ("relaxation_rate", 1.1),
    ("relaxation_rate", float("nan")), ("channel_height", 0),
    ("channel_height", float("inf")), ("max_speed", float("inf")),
    ("profile", "uniform"),
])
def test_velocity_rejects_invalid_parameters(field, value):
    payload = _heat_transfer_payload()
    payload["fluid_boundary_conditions"][0]["velocity"][field] = value
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate(payload)


@pytest.mark.parametrize("rate", [0.0, 1.0])
def test_velocity_accepts_relaxation_endpoints(rate):
    payload = _heat_transfer_payload()
    payload["fluid_boundary_conditions"][0]["velocity"]["relaxation_rate"] = rate
    assert SimulationConfig.model_validate(payload).fluid_boundary_conditions[0].velocity.relaxation_rate == rate


@pytest.mark.parametrize("field,value", [("type", "cosine"), ("time_constant", 0), ("time_constant", -1)])
def test_velocity_rejects_invalid_startup(field, value):
    payload = _heat_transfer_payload()
    payload["fluid_boundary_conditions"][0]["velocity"]["startup"][field] = value
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate(payload)


def test_bidirectional_requires_exactly_one_condition():
    payload = _heat_transfer_payload()
    inlet = payload["fluid_boundary_conditions"][0]
    inlet["pressure"] = 1.0
    with pytest.raises(ValidationError, match="exactly one"):
        SimulationConfig.model_validate(payload)
    del inlet["pressure"]
    del inlet["velocity"]
    with pytest.raises(ValidationError, match="exactly one"):
        SimulationConfig.model_validate(payload)


def test_velocity_rejects_emitter_boundary():
    payload = _heat_transfer_payload()
    payload["fluid_boundary_conditions"][0].update(type="emitter", inflow_speed=1.0)
    with pytest.raises(ValidationError, match="only supported for bi_directional"):
        SimulationConfig.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("duration", 0), ("duration", -1), ("duration", float("inf")),
    ("body_name", "WallBoundary"), ("target_velocity", [1.0]),
    ("target_velocity", [1.0, 0.0, 0.0]),
    ("target_velocity", [float("nan"), 0.0]),
])
def test_startup_acceleration_rejects_invalid_parameters(field, value):
    payload = _heat_transfer_payload()
    payload["startup_acceleration"][field] = value
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate(payload)


def test_startup_acceleration_rejects_gravity():
    payload = _heat_transfer_payload()
    payload["gravity"] = [0.0, -1.0]
    with pytest.raises(ValidationError, match="cannot be combined"):
        SimulationConfig.model_validate(payload)


def test_startup_acceleration_checks_explicit_domain_dimension():
    payload = _heat_transfer_payload()
    payload["geometries"]["system_domain"] = {
        "lower_bound": [0.0, 0.0], "upper_bound": [2.0, 0.4]
    }
    payload["startup_acceleration"]["target_velocity"] = [1.0, 0.0, 0.0]
    with pytest.raises(ValidationError, match="dimensionality"):
        SimulationConfig.model_validate(payload)


def test_velocity_rejects_mixture_material():
    payload = _heat_transfer_payload()
    payload["fluid_bodies"][0]["material"] = {
        "type": "weakly_compressible_multi_species",
        "species": [{"name": "Water", "density": 1.0}, {"name": "Other", "density": 2.0}],
    }
    with pytest.raises(ValidationError, match="velocity boundary requires"):
        SimulationConfig.model_validate(payload)
