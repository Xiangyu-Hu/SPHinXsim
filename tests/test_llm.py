"""Tests for the MockLLM natural-language → config conversion."""

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import PhysicsType, SimulationConfig
from sphinxsim.llm.mock_llm import MockLLM, _detect_physics


# ---------------------------------------------------------------------------
# _detect_physics helper
# ---------------------------------------------------------------------------


class TestDetectPhysics:
    def test_fluid_keywords(self):
        assert _detect_physics("water flowing through a pipe") == PhysicsType.FLUID
        assert _detect_physics("channel flow simulation") == PhysicsType.FLUID
        assert _detect_physics("Navier-Stokes solver") == PhysicsType.FLUID

    def test_solid_keywords(self):
        assert _detect_physics("elastic beam under load") == PhysicsType.SOLID
        assert _detect_physics("deformation of a steel plate") == PhysicsType.SOLID

    def test_fsi_keywords(self):
        assert _detect_physics("fsi simulation of a flexible flap") == PhysicsType.FSI
        assert _detect_physics("fluid-structure interaction") == PhysicsType.FSI

    def test_both_fluid_and_solid_yields_fsi(self):
        assert _detect_physics("water flow over an elastic structure") == PhysicsType.FSI

    def test_unknown_defaults_to_fluid(self):
        assert _detect_physics("some random text") == PhysicsType.FLUID


# ---------------------------------------------------------------------------
# MockLLM.generate
# ---------------------------------------------------------------------------


class TestMockLLM:
    def setup_method(self):
        self.llm = MockLLM()

    def test_returns_simulation_config(self):
        cfg = self.llm.generate("simulate water flowing through a pipe")
        assert isinstance(cfg, SimulationConfig)

    def test_physics_fluid(self):
        cfg = self.llm.generate("water dam break simulation")
        assert cfg.fluid_blocks[0].name

    def test_physics_solid(self):
        cfg = self.llm.generate("elastic beam bending under load")
        assert cfg.particle_spacing > 0

    def test_physics_fsi(self):
        cfg = self.llm.generate("hydroelastic fluid-structure interaction")
        assert cfg.end_time is not None

    def test_name_extracted(self):
        cfg = self.llm.generate("water flowing through a pipe at 2 m/s")
        assert len(cfg.fluid_blocks[0].name) > 0

    def test_velocity_override(self):
        cfg = self.llm.generate("water flowing at 3 m/s through a channel")
        assert cfg.fluid_blocks[0].sound_speed == pytest.approx(30.0)

    def test_end_time_override(self):
        cfg = self.llm.generate("simulate for 5 s")
        assert cfg.end_time == pytest.approx(5.0)

    def test_domain_override(self):
        cfg = self.llm.generate("simulate water in a 2 m domain")
        assert cfg.domain.dimensions == [2.0, 2.0]

    def test_resolution_override(self):
        cfg = self.llm.generate("water flow with 5 mm resolution")
        assert cfg.particle_spacing == pytest.approx(0.005)

    def test_empty_description_raises(self):
        with pytest.raises(ValueError, match="description must not be empty"):
            self.llm.generate("")

    def test_whitespace_description_raises(self):
        with pytest.raises(ValueError):
            self.llm.generate("   ")

    def test_result_is_valid_schema(self):
        """Generated config must always pass Pydantic validation."""
        descriptions = [
            "water through a pipe",
            "elastic plate vibration",
            "fsi simulation of a flag in the wind",
            "dam break",
            "tensile test of rubber in a 2 m domain",
            "water at 10 m/s for 2 s",
        ]
        for desc in descriptions:
            cfg = self.llm.generate(desc)
            # round-trip through JSON to confirm schema is fully satisfied
            restored = SimulationConfig.model_validate_json(cfg.model_dump_json())
            assert restored == cfg

    def test_update_changes_existing_end_time(self):
        base = self.llm.generate("water flow")
        updated = self.llm.update(base, "simulate for 3 s")
        assert updated.end_time == pytest.approx(3.0)

    def test_update_changes_end_time_with_second_wording(self):
        base = self.llm.generate("water flow")
        updated = self.llm.update(base, "the end time is 3 second.")
        assert updated.end_time == pytest.approx(3.0)

    def test_update_adds_observer(self):
        base = self.llm.generate("water flow")
        updated = self.llm.update(base, "add observer named outlet at (1.0, 0.5)")
        assert len(updated.observers) == len(base.observers) + 1
        assert updated.observers[-1].name == "outlet"
