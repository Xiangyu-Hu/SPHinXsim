"""Tests for OpenAI generation post-processing and validation repair."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import warnings

import pytest

from sphinxsim.llm.common import LLMRepairWarning, example_config
from sphinxsim.llm.openai_llm import OpenAILLM


PROMPT = (
    "Create a 3D landslide simulation using two STL files. "
    "Use landslides.stl to define the moving landslide body and boundary.stl "
    "to define the fixed terrain boundary. Assign the landslide material a "
    "density of 1800 kg/m\u00b3, a Young\u2019s modulus of 200 MPa, a "
    "Poisson\u2019s ratio of 0.3, a friction angle of 10.5 degrees, a cohesion "
    "of 15 kPa, and a dilatancy angle of 0 degrees. Set the particle spacing "
    "to 10 m, the end time to 80 s, and the output interval to 5 s."
)


def _response(payload: dict) -> SimpleNamespace:
    message = SimpleNamespace(content=json.dumps(payload))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _llm_with_responses(*payloads: dict) -> tuple[OpenAILLM, MagicMock]:
    create = MagicMock(side_effect=[_response(payload) for payload in payloads])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch("sphinxsim.llm.openai_llm.OpenAI", return_value=client):
        llm = OpenAILLM(api_key="test-key")
    return llm, create


def test_generate_applies_exact_unicode_soil_and_stl_values():
    llm, create = _llm_with_responses(example_config(PROMPT))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config = llm.generate(PROMPT)

    shapes = {shape.name: shape for shape in config.geometries.shapes}
    material = config.continuum_bodies[0].material
    assert shapes["GranularBody"].file_name == "landslides.stl"
    assert shapes["WallBoundary"].file_name == "boundary.stl"
    assert material.density == pytest.approx(1800.0)
    assert material.youngs_modulus == pytest.approx(200.0e6)
    assert material.poisson_ratio == pytest.approx(0.3)
    assert material.friction_angle == pytest.approx(math.radians(10.5))
    assert material.cohesion == pytest.approx(15.0e3)
    assert material.dilatancy_angle == pytest.approx(0.0)
    assert material.sound_speed is None
    assert config.geometries.global_resolution.particle_spacing == pytest.approx(10.0)
    assert config.solver_parameters.end_time == pytest.approx(80.0)
    assert config.solver_parameters.output_interval == pytest.approx(5.0)
    assert create.call_count == 1


def test_generate_sends_candidate_and_validation_errors_for_one_repair():
    description = "granular soil column collapse with plastic continuum"
    invalid = example_config(description)
    invalid["continuum_bodies"][0]["material"]["poisson_ratio"] = 0.6
    repaired = example_config(description)
    llm, create = _llm_with_responses(invalid, repaired)

    with pytest.warns(LLMRepairWarning, match="poisson_ratio: 0.6 -> 0.3"):
        config = llm.generate(description)

    assert config.continuum_bodies[0].material.poisson_ratio == pytest.approx(0.3)
    assert create.call_count == 2
    retry_messages = create.call_args_list[1].kwargs["messages"]
    retry_user = json.loads(retry_messages[1]["content"])
    assert retry_user["candidate_config"]["continuum_bodies"][0]["material"][
        "poisson_ratio"
    ] == pytest.approx(0.6)
    assert "0 <= poisson_ratio < 0.5" in json.dumps(retry_user["validation_errors"])
