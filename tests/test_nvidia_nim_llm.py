"""Tests for NvidiaNIMLLM (HTTP calls are fully mocked)."""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

import pytest

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm import get_llm
from sphinxsim.llm.common import example_config
from sphinxsim.llm.mock_llm import MockLLM
from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM


def _nim_response(content: Any) -> Dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }


def _make_response(payload: Dict[str, Any]):
    raw = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


_FLUID_CONFIG = MockLLM().generate("water dam break simulation")


class TestNvidiaNIMLLMInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="API key is required"):
            NvidiaNIMLLM(api_key=None)

    def test_defaults(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        assert llm.base_url == "https://integrate.api.nvidia.com/v1"
        assert llm.model == "z-ai/glm-5.2"
        assert llm.timeout == 60.0


class TestNvidiaNIMLLMGenerate:
    def setup_method(self):
        self.llm = NvidiaNIMLLM(api_key="test-key")

    def test_returns_simulation_config(self):
        resp = _make_response(_nim_response(_FLUID_CONFIG.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp):
            cfg = self.llm.generate("water dam break simulation")
        assert isinstance(cfg, SimulationConfig)

    def test_request_uses_expected_endpoint_and_headers(self):
        resp = _make_response(_nim_response(_FLUID_CONFIG.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            self.llm.generate("water flow")
        req = mock_open.call_args[0][0]
        assert req.full_url == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert req.get_header("Authorization") == "Bearer test-key"
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "z-ai/glm-5.2"
        assert body["stream"] is False

    def test_fenced_json_is_stripped(self):
        fenced = "```json\n" + _FLUID_CONFIG.model_dump_json(exclude_none=True) + "\n```"
        resp = _make_response(_nim_response(fenced))
        with patch("urllib.request.urlopen", return_value=resp):
            cfg = self.llm.generate("water flow")
        assert isinstance(cfg, SimulationConfig)

    def test_first_complete_json_object_is_used_when_text_is_appended(self):
        content = _FLUID_CONFIG.model_dump_json(exclude_none=True) + "\nGeneration complete."

        with pytest.warns(RuntimeWarning, match="Ignored trailing content"):
            parsed = self.llm._load_json_content(content)

        assert parsed["simulation_type"] == "fluid_dynamics"

    def test_incomplete_json_is_not_silently_rewritten(self):
        with pytest.raises(json.JSONDecodeError, match="Expecting ',' delimiter"):
            self.llm._load_json_content('{"simulation_type":"fluid_dynamics" "gravity":[0,-9.8]}')

    def test_two_json_objects_are_rejected_as_ambiguous(self):
        with pytest.raises(json.JSONDecodeError, match="Extra data"):
            self.llm._load_json_content('{"first":true}\n{"second":true}')

    def test_plastic_continuum_request_uses_soil_example_output(self):
        resp = _make_response(
            _nim_response(
                MockLLM().generate("granular soil column collapse").model_dump_json(exclude_none=True)
            )
        )
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            cfg = self.llm.generate("Plastic continumn granular soil column collapse")

        body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        user_content = json.loads(body["messages"][1]["content"])
        example_material = user_content["example_output"]["continuum_bodies"][0]["material"]
        assert example_material["type"] == "plastic_continuum"
        assert cfg.continuum_bodies[0].material.type.value == "plastic_continuum"

    def test_3d_request_uses_3d_example_output(self):
        resp = _make_response(_nim_response(_FLUID_CONFIG.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            self.llm.generate("3d dam break")

        body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        user_content = json.loads(body["messages"][1]["content"])
        example_output = user_content["example_output"]
        assert len(example_output["geometries"]["system_domain"]["lower_bound"]) == 3
        assert all(shape["type"] != "multipolygon" for shape in example_output["geometries"]["shapes"])

    def test_3d_plastic_request_uses_repose_angle_example_output(self):
        resp = _make_response(
            _nim_response(
                MockLLM().generate("granular soil column collapse").model_dump_json(exclude_none=True)
            )
        )
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            self.llm.generate("3d column collapse using plastic material")

        body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        user_content = json.loads(body["messages"][1]["content"])
        example_output = user_content["example_output"]
        assert example_output["simulation_type"] == "continuum_dynamics"
        assert len(example_output["geometries"]["system_domain"]["lower_bound"]) == 3
        assert example_output["continuum_bodies"][0]["material"]["type"] == "plastic_continuum"
        assert all(shape["type"] != "multipolygon" for shape in example_output["geometries"]["shapes"])

    def test_3d_stl_landslide_request_adapts_repose_angle_example_output(self):
        resp = _make_response(_nim_response("{}"))
        description = (
            "Create a 3D landslide simulation using two STL files. "
            "Use landslides.stl to define the moving landslide body and "
            "boundary.stl to define the fixed terrain boundary."
        )
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            config = self.llm.generate(description)

        body = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        user_content = json.loads(body["messages"][1]["content"])
        example_output = user_content["example_output"]
        shapes = {shape["name"]: shape for shape in example_output["geometries"]["shapes"]}
        assert "system_domain" in example_output["geometries"]
        assert shapes["GranularBody"]["type"] == "cylinder"
        assert shapes["WallBoundary"]["type"] == "complex_shape"

        generated_shapes = {shape.name: shape for shape in config.geometries.shapes}
        assert generated_shapes["GranularBody"].file_name == "landslides.stl"
        assert generated_shapes["WallBoundary"].file_name == "boundary.stl"
        particle_bodies = {body.name: body for body in config.particle_generation.settings.bodies}
        assert particle_bodies["GranularBody"].relaxation.level_set == {}
        assert particle_bodies["WallBoundary"].relaxation.level_set == {}

    def test_stl_landslide_request_generates_triangle_mesh_shapes(self):
        resp = _make_response(_nim_response("{}"))
        description = (
            "Create a runnable 3D landslide simulation from two STL files: "
            "./input/SlideBody.stl is the moving landslide soil body, and "
            "./input/Channel.stl is the fixed terrain boundary."
        )
        with patch("urllib.request.urlopen", return_value=resp):
            cfg = self.llm.generate(description)

        shapes = {shape.name: shape for shape in cfg.geometries.shapes}
        assert shapes["GranularBody"].type.value == "triangle_mesh"
        assert shapes["GranularBody"].file_name == "./input/SlideBody.stl"
        assert shapes["WallBoundary"].type.value == "triangle_mesh"
        assert shapes["WallBoundary"].file_name == "./input/Channel.stl"

    def test_network_error_raises_runtime_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib_error.URLError("connection refused")):
            with pytest.raises(RuntimeError, match="Failed to contact NVIDIA NIM"):
                self.llm.generate("water flow")

    def test_generate_repairs_malformed_shape_and_material(self):
        malformed = _FLUID_CONFIG.model_dump(exclude_none=True)
        malformed["simulation_type"] = "continuum_dynamics"
        malformed["geometries"]["shapes"][0]["type"] = "box"
        malformed["geometries"]["shapes"][0].pop("half_size", None)
        malformed["geometries"]["shapes"][0].pop("transform", None)
        malformed["continuum_bodies"] = [
            {
                "name": "column",
                "material": {
                    "type": "j2_plasticity",
                    "density": 2600.0,
                    "sound_speed": 100.0,
                    "youngs_modulus": 1000000.0,
                    "poisson_ratio": 0.3,
                    "yield_stress": 1000000.0,
                },
            }
        ]
        first = _make_response(_nim_response(json.dumps(malformed)))
        repaired = MockLLM().generate("granular soil column collapse")
        second = _make_response(_nim_response(repaired.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", side_effect=[first, second]) as mock_open:
            with pytest.warns(UserWarning, match="LLM repaired the generated config"):
                cfg = self.llm.generate("soil column collapse")
        assert isinstance(cfg, SimulationConfig)
        assert mock_open.call_count == 2

        retry_body = json.loads(mock_open.call_args_list[1][0][0].data.decode("utf-8"))
        retry_user = json.loads(retry_body["messages"][1]["content"])
        assert retry_user["description"] == "soil column collapse"
        assert retry_user["candidate_config"]["simulation_type"] == "continuum_dynamics"
        assert retry_user["validation_errors"]
        assert retry_user["example_output"]["continuum_bodies"][0]["material"]["type"] == "plastic_continuum"

    def test_generate_retries_only_once_then_uses_template_fallback(self):
        malformed = _FLUID_CONFIG.model_dump(exclude_none=True)
        malformed["simulation_type"] = "continuum_dynamics"
        malformed["geometries"]["shapes"][0]["type"] = "box"
        malformed["geometries"]["shapes"][0].pop("half_size", None)
        malformed["geometries"]["shapes"][0].pop("transform", None)

        first = _make_response(_nim_response(json.dumps(malformed)))
        second = _make_response(_nim_response(json.dumps(malformed)))
        with patch("urllib.request.urlopen", side_effect=[first, second]) as mock_open:
            cfg = self.llm.generate("soil column collapse")

        assert isinstance(cfg, SimulationConfig)
        assert cfg.simulation_type.value == "continuum_dynamics"
        assert cfg.continuum_bodies[0].material.type.value == "plastic_continuum"
        assert mock_open.call_count == 2

    def test_degraded_primary_model_falls_back_to_secondary(self):
        llm = NvidiaNIMLLM(
            api_key="test-key",
            model="z-ai/glm-5.2",
            fallback_models=("meta/llama-3.1-8b-instruct",),
        )
        degraded_payload = (
            b'{"status":400,"title":"Bad Request","detail":"Function id xyz: DEGRADED function cannot be invoked"}'
        )
        degraded_error = urllib_error.HTTPError(
            url="https://integrate.api.nvidia.com/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=degraded_payload)),
        )
        good_resp = _make_response(_nim_response(_FLUID_CONFIG.model_dump_json(exclude_none=True)))

        with patch("urllib.request.urlopen", side_effect=[degraded_error, good_resp]) as mock_open:
            cfg = llm.generate("water flow")

        assert isinstance(cfg, SimulationConfig)
        first_body = json.loads(mock_open.call_args_list[0][0][0].data.decode("utf-8"))
        second_body = json.loads(mock_open.call_args_list[1][0][0].data.decode("utf-8"))
        assert first_body["model"] == "z-ai/glm-5.2"
        assert second_body["model"] == "meta/llama-3.1-8b-instruct"

    def test_degraded_without_fallback_raises_actionable_error(self):
        degraded_payload = (
            b'{"status":400,"title":"Bad Request","detail":"Function id xyz: DEGRADED function cannot be invoked"}'
        )
        degraded_error = urllib_error.HTTPError(
            url="https://integrate.api.nvidia.com/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=degraded_payload)),
        )

        with patch("urllib.request.urlopen", side_effect=degraded_error):
            with pytest.raises(RuntimeError, match="NVIDIA_NIM_FALLBACK_MODELS"):
                self.llm.generate("water flow")


class TestNvidiaNIMLLMExplore:
    def test_returns_string_answer(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        resp = _make_response(_nim_response("Use fluid_dynamics for free-surface water simulations."))
        with patch("urllib.request.urlopen", return_value=resp):
            answer = llm.explore("what should I use for water?")
        assert "fluid_dynamics" in answer


class TestNvidiaNIMLLMUpdate:
    def test_update_simulation_type_intent_with_typo_is_applied(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        base = MockLLM().generate("water dam break simulation")
        # Return unchanged payload to force local coercion path.
        resp = _make_response(_nim_response(base.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp):
            updated = llm.update(base, "change simulaiton type to continuum dynamics")

        assert updated.simulation_type.value == "continuum_dynamics"
        assert updated.solver_parameters.continuum_dynamics is not None
        assert len(updated.continuum_bodies) >= 1
        assert len(updated.fluid_bodies) >= 1

    def test_update_plastic_continuum_intent_creates_continuum_body(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        base = MockLLM().generate("water dam break simulation")
        resp = _make_response(_nim_response(base.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp):
            updated = llm.update(
                base,
                "I want a 3d column collapse case, matertialtype is plastic_continuum",
            )

        assert updated.simulation_type.value == "continuum_dynamics"
        assert updated.continuum_bodies[0].material.type.value == "plastic_continuum"
        assert not updated.fluid_bodies

    def test_update_stl_landslide_request_replaces_shapes(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        base = SimulationConfig.model_validate(example_config("3d landslide case"))
        resp = _make_response(_nim_response("{}"))
        with patch("urllib.request.urlopen", return_value=resp):
            updated = llm.update(
                base,
                "Use ./input/SlideBody.stl as the landslide body and ./input/Channel.stl as the terrain.",
            )

        shapes = {shape.name: shape for shape in updated.geometries.shapes}
        assert shapes["GranularBody"].type.value == "triangle_mesh"
        assert shapes["GranularBody"].file_name == "./input/SlideBody.stl"
        assert shapes["WallBoundary"].type.value == "triangle_mesh"
        assert shapes["WallBoundary"].file_name == "./input/Channel.stl"

    def test_update_retry_payload_serializes_validation_errors(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        base = MockLLM().generate("water dam break simulation")

        bad_update = base.model_dump(exclude_none=True)
        bad_update["geometries"]["shapes"][0]["name"] = "SoilBody"
        first = _make_response(_nim_response(json.dumps(bad_update)))
        second = _make_response(_nim_response(base.model_dump_json(exclude_none=True)))

        with patch("urllib.request.urlopen", side_effect=[first, second]):
            updated = llm.update(base, "please change the shape name WaterBody to SoilBody")

        assert isinstance(updated, SimulationConfig)

    def test_update_shape_rename_propagates_references(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        base = MockLLM().generate("water dam break simulation")

        # Return unchanged payload to force deterministic rename propagation.
        resp = _make_response(_nim_response(base.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp):
            updated = llm.update(base, "please change the shape name WaterBody to SoilBody")

        assert isinstance(updated, SimulationConfig)
        assert any(shape.name == "SoilBody" for shape in updated.geometries.shapes)
        assert all(body.name != "WaterBody" for body in updated.fluid_bodies)
        assert any(body.name == "SoilBody" for body in updated.fluid_bodies)
        assert all(observer.observed_body != "WaterBody" for observer in updated.observers)

    def test_update_shape_rename_propagates_for_other_shape_names(self):
        llm = NvidiaNIMLLM(api_key="test-key")
        base = MockLLM().generate("water dam break simulation")

        # Return unchanged payload to force deterministic rename propagation.
        resp = _make_response(_nim_response(base.model_dump_json(exclude_none=True)))
        with patch("urllib.request.urlopen", return_value=resp):
            updated = llm.update(base, "please change the shape name WallBoundary to RockBoundary")

        assert isinstance(updated, SimulationConfig)
        assert any(shape.name == "RockBoundary" for shape in updated.geometries.shapes)
        assert all(body.name != "WallBoundary" for body in updated.solid_bodies)
        assert any(body.name == "RockBoundary" for body in updated.solid_bodies)
        pg_bodies = updated.particle_generation.settings.bodies if updated.particle_generation.settings else []
        assert any(body.name == "RockBoundary" for body in pg_bodies)


class TestGetLLMNvidiaNIM:
    def test_get_llm_returns_nvidia_nim_when_env_set(self, monkeypatch):
        monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "nvidia_nim")
        monkeypatch.setenv("NVIDIA_NIM_API_KEY", "test-key")
        llm = get_llm()
        assert isinstance(llm, NvidiaNIMLLM)
    def test_get_llm_accepts_legacy_nvidia_api_key_env(self, monkeypatch):
        monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "nvidia_nim")
        monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        llm = get_llm()
        assert isinstance(llm, NvidiaNIMLLM)
