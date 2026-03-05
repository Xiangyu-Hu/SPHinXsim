"""Tests for the SimulationBuilder high-level API."""

import pytest

try:
    from sphinxsim.bindings import SimulationBuilder, SimulationResult
    from sphinxsim.bindings.simulation_builder import _CPP_AVAILABLE
except RuntimeError:
    _CPP_AVAILABLE = False


# Skip all tests if C++ extension is not available
pytestmark = pytest.mark.skipif(
    not _CPP_AVAILABLE,
    reason="C++ extension _sphinxsys_core not available"
)


class TestSimulationBuilder:
    """Tests for SimulationBuilder functionality."""
    
    def test_builder_initialization(self):
        """Test that SimulationBuilder can be initialized."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        assert builder is not None
        assert repr(builder).startswith("<SimulationBuilder")
    
    def test_add_fluid_block(self):
        """Test adding a fluid block."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        builder.add_fluid_block(
            name="water",
            block_size=[5.0, 2.0],
            density=1000.0,
            sound_speed=100.0
        )
        
        assert len(builder._fluid_blocks) == 1
        fluid = builder._fluid_blocks[0]
        assert fluid.get_name() == "water"
        assert fluid.get_density() == pytest.approx(1000.0)
        assert fluid.get_sound_speed() == pytest.approx(100.0)
    
    def test_add_wall_boundary(self):
        """Test adding a wall boundary."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        builder.add_wall_boundary(
            name="tank",
            domain_size=[10.0, 5.0],
            wall_thickness=0.4
        )
        
        assert len(builder._walls) == 1
        wall = builder._walls[0]
        assert wall.get_name() == "tank"
        assert wall.get_wall_thickness() == pytest.approx(0.4)
    
    def test_set_gravity(self):
        """Test setting gravity."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        builder.set_gravity([0.0, -9.81])
        
        assert builder._gravity == (0.0, -9.81)
    
    def test_add_observer(self):
        """Test adding an observer."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        builder.add_observer(name="sensor", position=[5.0, 2.5])
        
        assert len(builder._observers) == 1
        assert builder._observers[0] == ("sensor", (5.0, 2.5))
    
    def test_method_chaining(self):
        """Test that builder methods support chaining."""
        builder = (
            SimulationBuilder(domain_size=[10.0, 5.0], particle_spacing=0.1)
            .add_fluid_block(
                name="water",
                block_size=[5.0, 2.0],
                density=1000.0,
                sound_speed=100.0
            )
            .add_wall_boundary(
                name="tank",
                domain_size=[10.0, 5.0],
                wall_thickness=0.4
            )
            .set_gravity([0.0, -9.81])
            .add_observer(name="sensor", position=[5.0, 2.5])
            .enable_dual_time_stepping()
            .enable_free_surface_correction()
        )
        
        assert len(builder._fluid_blocks) == 1
        assert len(builder._walls) == 1
        assert builder._gravity == (0.0, -9.81)
        assert len(builder._observers) == 1
    
    def test_solver_configuration(self):
        """Test solver configuration."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        solver = builder.configure_solver()
        
        assert solver is not None
        assert repr(solver).startswith("<SolverConfiguration")
    
    def test_run_creates_result(self, build_temp_path):
        """Test that run() returns a SimulationResult."""
        import os
        
        builder = (
            SimulationBuilder(
                domain_size=[10.0, 5.0],
                particle_spacing=0.1
            )
            .add_fluid_block(
                name="water",
                block_size=[5.0, 2.0],
                density=1000.0,
                sound_speed=100.0
            )
            .add_wall_boundary(
                name="tank",
                domain_size=[10.0, 5.0],
                wall_thickness=0.4
            )
            .set_gravity([0.0, -9.81])
        )
        
        # Change to temp directory to capture SPHinXsys outputs
        original_dir = os.getcwd()
        try:
            os.chdir(build_temp_path)
            result = builder.run(end_time=0.1)
        finally:
            os.chdir(original_dir)
        
        assert isinstance(result, SimulationResult)
        assert result.end_time == pytest.approx(0.1)
        assert len(result.fluid_blocks) == 1
        assert len(result.walls) == 1
        assert result.gravity == (0.0, -9.81)


class TestFluidBlock:
    """Tests for FluidBlock wrapper."""
    
    def test_fluid_block_repr(self):
        """Test FluidBlock string representation."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        builder.add_fluid_block(
            name="water",
            block_size=[5.0, 2.0],
            density=1000.0,
            sound_speed=100.0
        )
        
        fluid = builder._fluid_blocks[0]
        repr_str = repr(fluid)
        assert "FluidBlock" in repr_str
        assert "water" in repr_str


class TestWallBoundary:
    """Tests for WallBoundary wrapper."""
    
    def test_wall_boundary_repr(self):
        """Test WallBoundary string representation."""
        builder = SimulationBuilder(
            domain_size=[10.0, 5.0],
            particle_spacing=0.1
        )
        builder.add_wall_boundary(
            name="tank",
            domain_size=[10.0, 5.0],
            wall_thickness=0.4
        )
        
        wall = builder._walls[0]
        repr_str = repr(wall)
        assert "WallBoundary" in repr_str
        assert "tank" in repr_str
