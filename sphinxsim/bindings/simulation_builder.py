"""High-level Python wrapper for the SPHinXsys C++ simulation builder.

This module provides a Pythonic interface to the C++ simulation API,
abstracting away the builder pattern and providing convenient methods
for constructing and running simulations.

Example usage::

    builder = SimulationBuilder(domain_size=[10.0, 5.0], particle_spacing=0.1)
    builder.add_fluid_block(
        name="water",
        block_size=[5.0, 2.0],
        density=1000.0,
        sound_speed=100.0
    )
    builder.add_wall_boundary(
        name="tank",
        domain_size=[10.0, 5.0],
        wall_thickness=0.4
    )
    builder.set_gravity([0.0, -9.81])
    builder.add_observer(name="sensor", position=[5.0, 2.5])
    builder.enable_dual_time_stepping()
    builder.enable_free_surface_correction()
    
    result = builder.run(end_time=2.0, output_prefix="simulation_output")
"""

from __future__ import annotations

from typing import Optional, List, Tuple, Union
from pathlib import Path

try:
    import _sphinxsys_core as _core
    _CPP_AVAILABLE = True
except ImportError:
    _core = None
    _CPP_AVAILABLE = False


class FluidBlock:
    """Represents a configured fluid block in the simulation."""
    
    def __init__(self, builder: _core.FluidBlockBuilder) -> None:
        """Initialize from a C++ FluidBlockBuilder.
        
        Parameters
        ----------
        builder : _core.FluidBlockBuilder
            The underlying C++ builder object.
        """
        self._builder = builder
    
    def get_name(self) -> str:
        """Get the fluid block name."""
        return self._builder.getName()
    
    def get_dimensions(self) -> Tuple[float, float]:
        """Get the fluid block dimensions."""
        dims = self._builder.getDimensions()
        return (float(dims[0]), float(dims[1]))
    
    def get_density(self) -> float:
        """Get the reference density."""
        return float(self._builder.getRho0())
    
    def get_sound_speed(self) -> float:
        """Get the sound speed."""
        return float(self._builder.getC())
    
    def __repr__(self) -> str:
        return (
            f"<FluidBlock name={self.get_name()!r} "
            f"dims={self.get_dimensions()} "
            f"rho0={self.get_density()} "
            f"c={self.get_sound_speed()}>"
        )


class WallBoundary:
    """Represents a configured wall boundary in the simulation."""
    
    def __init__(self, builder: _core.WallBuilder) -> None:
        """Initialize from a C++ WallBuilder.
        
        Parameters
        ----------
        builder : _core.WallBuilder
            The underlying C++ builder object.
        """
        self._builder = builder
    
    def get_name(self) -> str:
        """Get the wall name."""
        return self._builder.getName()
    
    def get_domain_dimensions(self) -> Tuple[float, float]:
        """Get the wall domain dimensions."""
        dims = self._builder.getDomainDimensions()
        return (float(dims[0]), float(dims[1]))
    
    def get_wall_thickness(self) -> float:
        """Get the wall thickness."""
        return float(self._builder.getWallWidth())
    
    def __repr__(self) -> str:
        return (
            f"<WallBoundary name={self.get_name()!r} "
            f"domain={self.get_domain_dimensions()} "
            f"thickness={self.get_wall_thickness()}>"
        )


class SolverConfiguration:
    """Represents the solver configuration."""
    
    def __init__(self, config: _core.SolverConfig) -> None:
        """Initialize from a C++ SolverConfig.
        
        Parameters
        ----------
        config : _core.SolverConfig
            The underlying C++ SolverConfig object.
        """
        self._config = config
    
    def enable_dual_time_stepping(self) -> SolverConfiguration:
        """Enable dual time stepping (advection + acoustic sub-stepping).
        
        Returns
        -------
        SolverConfiguration
            Self for method chaining.
        """
        self._config.dualTimeStepping()
        return self
    
    def enable_free_surface_correction(self) -> SolverConfiguration:
        """Enable density summation with free-surface correction.
        
        Returns
        -------
        SolverConfiguration
            Self for method chaining.
        """
        self._config.freeSurfaceCorrection()
        return self
    
    def is_dual_time_stepping_enabled(self) -> bool:
        """Check if dual time stepping is enabled."""
        return bool(self._config.isDualTimeStepping())
    
    def is_free_surface_correction_enabled(self) -> bool:
        """Check if free surface correction is enabled."""
        return bool(self._config.isFreeSurfaceCorrection())
    
    def __repr__(self) -> str:
        return (
            f"<SolverConfiguration "
            f"dual_time_stepping={self.is_dual_time_stepping_enabled()} "
            f"free_surface_correction={self.is_free_surface_correction_enabled()}>"
        )


class SimulationBuilder:
    """High-level builder for constructing and running SPHinXsys simulations.
    
    This class provides a fluent interface for setting up simulations,
    abstracting the builder pattern from the C++ bindings.
    """
    
    def __init__(
        self,
        domain_size: Union[List[float], Tuple[float, float]],
        particle_spacing: float,
        output_prefix: Optional[str] = None,
    ) -> None:
        """Initialize a simulation builder.
        
        Parameters
        ----------
        domain_size : list or tuple of float
            Domain dimensions [width, height] in meters.
        particle_spacing : float
            Reference particle spacing in meters.
        output_prefix : str, optional
            Prefix for output directories. If None, uses default location.
        
        Raises
        ------
        RuntimeError
            If C++ extension is not available.
        """
        if not _CPP_AVAILABLE:
            raise RuntimeError(
                "C++ extension _sphinxsys_core not available. "
                "Please build the project: ninja -C build-integrated"
            )
        
        self._sim = _core.SPHSimulation()
        self._sim.createDomain(list(domain_size), float(particle_spacing))
        
        if output_prefix:
            self._sim.setOutputPrefix(output_prefix)
        
        self._fluid_blocks: List[FluidBlock] = []
        self._walls: List[WallBoundary] = []
        self._solver: Optional[SolverConfiguration] = None
        self._gravity: Optional[Tuple[float, float]] = None
        self._observers: List[Tuple[str, Tuple[float, float]]] = []
    
    def add_fluid_block(
        self,
        name: str,
        block_size: Union[List[float], Tuple[float, float]],
        density: float,
        sound_speed: float,
    ) -> SimulationBuilder:
        """Add a fluid block to the simulation.
        
        Parameters
        ----------
        name : str
            Name of the fluid block.
        block_size : list or tuple of float
            Block dimensions [width, height].
        density : float
            Reference fluid density (kg/m³).
        sound_speed : float
            Artificial sound speed for weakly-compressible formulation (m/s).
        
        Returns
        -------
        SimulationBuilder
            Self for method chaining.
        """
        builder = self._sim.addFluidBlock(name)
        builder.block(list(block_size))
        builder.material(float(density), float(sound_speed))
        
        fluid = FluidBlock(builder)
        self._fluid_blocks.append(fluid)
        return self
    
    def add_wall_boundary(
        self,
        name: str,
        domain_size: Union[List[float], Tuple[float, float]],
        wall_thickness: float,
    ) -> SimulationBuilder:
        """Add a wall boundary (hollow box) to the simulation.
        
        Parameters
        ----------
        name : str
            Name of the wall.
        domain_size : list or tuple of float
            Domain dimensions [width, height] aligned with origin.
        wall_thickness : float
            Thickness of the wall.
        
        Returns
        -------
        SimulationBuilder
            Self for method chaining.
        """
        builder = self._sim.addWall(name)
        builder.hollowBox(list(domain_size), float(wall_thickness))
        
        wall = WallBoundary(builder)
        self._walls.append(wall)
        return self
    
    def set_gravity(
        self,
        gravity: Union[List[float], Tuple[float, float]],
    ) -> SimulationBuilder:
        """Set uniform gravitational acceleration.
        
        Parameters
        ----------
        gravity : list or tuple of float
            Gravity vector [gx, gy] in m/s².
        
        Returns
        -------
        SimulationBuilder
            Self for method chaining.
        """
        gravity_vec = list(gravity)
        self._sim.enableGravity(gravity_vec)
        self._gravity = (float(gravity_vec[0]), float(gravity_vec[1]))
        return self
    
    def add_observer(
        self,
        name: str,
        position: Union[List[float], Tuple[float, float]],
    ) -> SimulationBuilder:
        """Add a point observer for monitoring.
        
        Parameters
        ----------
        name : str
            Name of the observer.
        position : list or tuple of float
            Observer position [x, y].
        
        Returns
        -------
        SimulationBuilder
            Self for method chaining.
        """
        pos_vec = list(position)
        self._sim.addObserver(name, pos_vec)
        self._observers.append((name, (float(pos_vec[0]), float(pos_vec[1]))))
        return self
    
    def configure_solver(self) -> SolverConfiguration:
        """Get the solver configuration for advanced settings.
        
        Returns
        -------
        SolverConfiguration
            Solver configuration object for method chaining.
        
        Example
        -------
        >>> builder.configure_solver().enable_dual_time_stepping().enable_free_surface_correction()
        """
        if self._solver is None:
            config = self._sim.useSolver()
            self._solver = SolverConfiguration(config)
        return self._solver
    
    def enable_dual_time_stepping(self) -> SimulationBuilder:
        """Enable dual time stepping for better accuracy.
        
        Returns
        -------
        SimulationBuilder
            Self for method chaining.
        """
        self.configure_solver().enable_dual_time_stepping()
        return self
    
    def enable_free_surface_correction(self) -> SimulationBuilder:
        """Enable free surface correction for fluid dynamics.
        
        Returns
        -------
        SimulationBuilder
            Self for method chaining.
        """
        self.configure_solver().enable_free_surface_correction()
        return self
    
    def run(self, end_time: float) -> SimulationResult:
        """Run the simulation to the specified end time.
        
        Parameters
        ----------
        end_time : float
            Simulation end time in seconds.
        
        Returns
        -------
        SimulationResult
            Result object containing simulation statistics and outputs.
        """
        self._sim.run(float(end_time))
        
        return SimulationResult(
            end_time=end_time,
            fluid_blocks=self._fluid_blocks,
            walls=self._walls,
            gravity=self._gravity,
            observers=self._observers,
        )
    
    def __repr__(self) -> str:
        return (
            f"<SimulationBuilder "
            f"fluid_blocks={len(self._fluid_blocks)} "
            f"walls={len(self._walls)} "
            f"gravity={self._gravity}>"
        )


class SimulationResult:
    """Result of a completed simulation."""
    
    def __init__(
        self,
        end_time: float,
        fluid_blocks: List[FluidBlock],
        walls: List[WallBoundary],
        gravity: Optional[Tuple[float, float]],
        observers: List[Tuple[str, Tuple[float, float]]],
    ) -> None:
        """Initialize a simulation result.
        
        Parameters
        ----------
        end_time : float
            Simulation end time.
        fluid_blocks : list of FluidBlock
            Configured fluid blocks.
        walls : list of WallBoundary
            Configured wall boundaries.
        gravity : tuple or None
            Applied gravity vector.
        observers : list of tuple
            Configured observers.
        """
        self.end_time = end_time
        self.fluid_blocks = fluid_blocks
        self.walls = walls
        self.gravity = gravity
        self.observers = observers
    
    def __repr__(self) -> str:
        return (
            f"<SimulationResult "
            f"end_time={self.end_time} "
            f"fluid_blocks={len(self.fluid_blocks)} "
            f"walls={len(self.walls)}>"
        )
