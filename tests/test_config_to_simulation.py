"""Tests for config_to_simulation converter."""

import pytest
from pathlib import Path

from sphinxsim.config.schemas import (
    BoundaryCondition,
    BoundaryType,
    DomainConfig,
    MaterialConfig,
    OutputConfig,
    PhysicsType,
    SimulationConfig,
    TimeSteppingConfig,
)

try:
    from sphinxsim.llm.config_to_simulation import (
        config_to_builder,
        run_from_config,
        _is_dam_break_scenario,
    )
    from sphinxsim.bindings.simulation_builder import _CPP_AVAILABLE
except (ImportError, RuntimeError):
    _CPP_AVAILABLE = False


# Skip all tests if C++ extension is not available
pytestmark = pytest.mark.skipif(
    not _CPP_AVAILABLE,
    reason="C++ extension _sphinxsys_core not available"
)


class TestConfigToBuilder:
    """Tests for config_to_builder converter."""
    
    def test_simple_fluid_config(self):
        """Test converting a simple fluid configuration."""
        config = SimulationConfig(
            name="test_fluid",
            physics=PhysicsType.FLUID,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[2.0, 1.0],
                resolution=0.05
            ),
            materials=[
                MaterialConfig(
                    name="water",
                    density=1000.0,
                    dynamic_viscosity=1e-3
                )
            ],
            boundary_conditions=[
                BoundaryCondition(name="walls", type=BoundaryType.WALL)
            ],
            time_stepping=TimeSteppingConfig(
                end_time=1.0,
                output_interval=0.1
            ),
            output=OutputConfig(
                directory="./output",
                format="vtp"
            )
        )
        
        builder = config_to_builder(config)
        
        assert builder is not None
        assert len(builder._fluid_blocks) == 1
        assert len(builder._walls) == 1
        assert builder._gravity is not None
    
    def test_dam_break_detection(self):
        """Test dam break scenario detection."""
        dam_config = SimulationConfig(
            name="water dam break simulation",
            physics=PhysicsType.FLUID,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[5.0, 5.0],
                resolution=0.025
            ),
            materials=[
                MaterialConfig(name="water", density=1000.0, dynamic_viscosity=1e-3)
            ],
            boundary_conditions=[],
            time_stepping=TimeSteppingConfig(end_time=1.0, output_interval=0.1),
            output=OutputConfig(directory="./output", format="vtp")
        )
        
        assert _is_dam_break_scenario(dam_config) is True
        
        # Non-dam-break scenario
        flow_config = SimulationConfig(
            name="channel flow",
            physics=PhysicsType.FLUID,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[2.0, 1.0],
                resolution=0.02
            ),
            materials=[
                MaterialConfig(name="water", density=1000.0, dynamic_viscosity=1e-3)
            ],
            boundary_conditions=[],
            time_stepping=TimeSteppingConfig(end_time=1.0, output_interval=0.1),
            output=OutputConfig(directory="./output", format="vtp")
        )
        
        assert _is_dam_break_scenario(flow_config) is False
    
    def test_solid_physics_not_supported(self):
        """Test that solid physics raises NotImplementedError."""
        config = SimulationConfig(
            name="solid_test",
            physics=PhysicsType.SOLID,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[1.0, 0.2],
                resolution=0.01
            ),
            materials=[
                MaterialConfig(
                    name="steel",
                    density=7850.0,
                    youngs_modulus=2e11,
                    poisson_ratio=0.3
                )
            ],
            boundary_conditions=[],
            time_stepping=TimeSteppingConfig(end_time=0.5, output_interval=0.05),
            output=OutputConfig(directory="./output", format="vtu")
        )
        
        with pytest.raises(NotImplementedError, match="Solid mechanics"):
            config_to_builder(config)
    
    def test_fsi_physics_not_supported(self):
        """Test that FSI physics raises NotImplementedError."""
        config = SimulationConfig(
            name="fsi_test",
            physics=PhysicsType.FSI,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[2.0, 1.0],
                resolution=0.02
            ),
            materials=[
                MaterialConfig(name="water", density=1000.0, dynamic_viscosity=1e-3),
                MaterialConfig(
                    name="plate",
                    density=1100.0,
                    youngs_modulus=1e6,
                    poisson_ratio=0.4
                )
            ],
            boundary_conditions=[],
            time_stepping=TimeSteppingConfig(end_time=1.0, output_interval=0.1),
            output=OutputConfig(directory="./output", format="vtp")
        )
        
        with pytest.raises(NotImplementedError, match="FSI"):
            config_to_builder(config)
    
    def test_no_fluid_material_raises(self):
        """Test that missing fluid material raises ValueError."""
        config = SimulationConfig(
            name="no_fluid",
            physics=PhysicsType.FLUID,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[1.0, 1.0],
                resolution=0.02
            ),
            materials=[
                MaterialConfig(
                    name="steel",
                    density=7850.0,
                    youngs_modulus=2e11,
                    poisson_ratio=0.3
                )
            ],
            boundary_conditions=[],
            time_stepping=TimeSteppingConfig(end_time=1.0, output_interval=0.1),
            output=OutputConfig(directory="./output", format="vtp")
        )
        
        with pytest.raises(ValueError, match="fluid material"):
            config_to_builder(config)


class TestRunFromConfig:
    """Tests for run_from_config wrapper."""
    
    def test_run_simple_simulation(self, tmp_path):
        """Test running a complete simulation from config."""
        config = SimulationConfig(
            name="test_run",
            physics=PhysicsType.FLUID,
            domain=DomainConfig(
                bounds_min=[0.0, 0.0],
                bounds_max=[2.0, 1.0],
                resolution=0.1
            ),
            materials=[
                MaterialConfig(name="water", density=1000.0, dynamic_viscosity=1e-3)
            ],
            boundary_conditions=[
                BoundaryCondition(name="walls", type=BoundaryType.WALL)
            ],
            time_stepping=TimeSteppingConfig(
                end_time=0.1,
                output_interval=0.1
            ),
            output=OutputConfig(directory=str(tmp_path), format="vtp")
        )
        
        builder, result = run_from_config(config, output_dir=tmp_path)
        
        assert builder is not None
        assert result is not None
        assert result.end_time == pytest.approx(0.1)
        assert len(result.fluid_blocks) == 1
        assert len(result.walls) == 1
