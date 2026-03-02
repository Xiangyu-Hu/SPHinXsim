"""Convert SimulationConfig schemas to executable SimulationBuilder instances.

This module bridges the LLM-generated configuration (which is a pure data model)
to the actual C++ simulation execution via SimulationBuilder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sphinxsim.config.schemas import (
    BoundaryType,
    PhysicsType,
    SimulationConfig,
)
from sphinxsim.bindings import SimulationBuilder


def config_to_builder(
    config: SimulationConfig,
    output_dir: Optional[Path] = None,
) -> SimulationBuilder:
    """Convert a SimulationConfig to a SimulationBuilder ready to run.
    
    This function translates the high-level configuration schema into
    the specific C++ API calls required to set up the simulation.
    
    Parameters
    ----------
    config : SimulationConfig
        The validated simulation configuration from LLM or user input.
    output_dir : Path, optional
        Directory for simulation output. If None, uses config.output.directory.
        
    Returns
    -------
    SimulationBuilder
        Configured simulation builder ready to run.
        
    Raises
    ------
    ValueError
        If the configuration contains unsupported features or invalid parameters.
    RuntimeError
        If C++ extension is not available.
        
    Notes
    -----
    Currently supports:
    - Fluid simulations with weakly-compressible formulation
    - Rectangular domains with uniform particle spacing
    - Wall boundaries (hollow boxes)
    - Inlet/outlet boundary conditions (via gravity/initial conditions)
    - Single fluid material
    
    Not yet supported:
    - Solid mechanics
    - FSI coupling
    - Complex geometries
    - Multiple fluids
    - Custom boundary shapes
    
    Example
    -------
    >>> from sphinxsim.llm.mock_llm import MockLLM
    >>> llm = MockLLM()
    >>> config = llm.generate("water dam break for 2 seconds")
    >>> builder = config_to_builder(config)
    >>> result = builder.run(end_time=config.time_stepping.end_time)
    """
    # Calculate domain size and particle spacing
    domain_min = config.domain.bounds_min
    domain_max = config.domain.bounds_max
    domain_size = [domain_max[i] - domain_min[i] for i in range(len(domain_max))]
    particle_spacing = config.domain.resolution
    
    # Determine output directory
    if output_dir is None:
        output_dir = Path(config.output.directory)
    else:
        output_dir = Path(output_dir)
    
    # Create base simulation builder
    builder = SimulationBuilder(
        domain_size=domain_size,
        particle_spacing=particle_spacing,
        output_prefix=None  # Will set working directory instead
    )
    
    # Handle different physics types
    if config.physics == PhysicsType.FLUID:
        _configure_fluid_simulation(builder, config, domain_size)
    elif config.physics == PhysicsType.SOLID:
        raise NotImplementedError(
            "Solid mechanics simulations not yet supported in SimulationBuilder. "
            "This requires binding solid body APIs from SPHinXsys."
        )
    elif config.physics == PhysicsType.FSI:
        raise NotImplementedError(
            "FSI simulations not yet supported in SimulationBuilder. "
            "This requires binding coupled fluid-solid APIs from SPHinXsys."
        )
    
    return builder


def _configure_fluid_simulation(
    builder: SimulationBuilder,
    config: SimulationConfig,
    domain_size: list[float],
) -> None:
    """Configure a fluid-only simulation.
    
    Parameters
    ----------
    builder : SimulationBuilder
        The builder to configure.
    config : SimulationConfig
        The simulation configuration.
    domain_size : list of float
        Domain dimensions [width, height].
    """
    # Get fluid material properties
    fluid_materials = [m for m in config.materials if m.dynamic_viscosity is not None]
    if not fluid_materials:
        raise ValueError("Fluid simulation requires at least one fluid material")
    
    fluid = fluid_materials[0]  # Use first fluid material
    
    # Calculate sound speed for weakly-compressible formulation
    # Use c = 10 * U_max as a typical rule of thumb
    # Extract characteristic velocity from inlet BC or use default
    u_max = 1.0
    for bc in config.boundary_conditions:
        if bc.type == BoundaryType.INLET and bc.velocity:
            u_max = max(abs(v) for v in bc.velocity)
            break
    
    sound_speed = 10.0 * max(u_max, 0.1)  # Minimum 1.0 m/s
    
    # Determine fluid block size from domain and boundary conditions
    # For dam break: use partial domain
    # For channel flow: use full domain
    if _is_dam_break_scenario(config):
        # Dam break: water column in left portion of domain
        fluid_width = domain_size[0] * 0.4  # 40% of domain width
        fluid_height = domain_size[1] * 0.5  # 50% of domain height
    else:
        # Channel flow: fill most of domain
        fluid_width = domain_size[0] * 0.9
        fluid_height = domain_size[1] * 0.8
    
    # Add fluid block
    builder.add_fluid_block(
        name=fluid.name,
        block_size=[fluid_width, fluid_height],
        density=fluid.density,
        sound_speed=sound_speed,
    )
    
    # Add wall boundaries
    # Look for wall boundaries in config, or create default tank walls
    wall_thickness = config.domain.resolution * 4  # Standard SPH wall thickness
    
    has_walls = any(bc.type == BoundaryType.WALL for bc in config.boundary_conditions)
    if has_walls or _is_dam_break_scenario(config):
        builder.add_wall_boundary(
            name="walls",
            domain_size=domain_size,
            wall_thickness=wall_thickness,
        )
    
    # Set gravity (common in fluid simulations)
    # Use standard gravity unless overridden
    gravity_magnitude = 9.81
    builder.set_gravity([0.0, -gravity_magnitude])
    
    # Add observer points for monitoring
    # Place observer at a characteristic location
    obs_x = domain_size[0] * 0.9  # Near right edge
    obs_y = domain_size[1] * 0.2  # Near bottom
    builder.add_observer(
        name="observer",
        position=[obs_x, obs_y]
    )
    
    # Configure solver
    builder.enable_dual_time_stepping()
    builder.enable_free_surface_correction()


def _is_dam_break_scenario(config: SimulationConfig) -> bool:
    """Detect if configuration describes a dam break scenario.
    
    Parameters
    ----------
    config : SimulationConfig
        The simulation configuration.
        
    Returns
    -------
    bool
        True if this appears to be a dam break scenario.
    """
    name_lower = config.name.lower()
    return any(keyword in name_lower for keyword in ['dam', 'break', 'collapse'])


def run_from_config(
    config: SimulationConfig,
    output_dir: Optional[Path] = None,
    change_dir: bool = True,
) -> tuple[SimulationBuilder, object]:
    """Complete workflow: convert config to builder and run simulation.
    
    Parameters
    ----------
    config : SimulationConfig
        The validated simulation configuration.
    output_dir : Path, optional
        Directory for simulation output. Defaults to .build-temp/simulations/<name>
    change_dir : bool, default=True
        Whether to change working directory to output_dir before running.
        
    Returns
    -------
    builder : SimulationBuilder
        The configured builder that was used.
    result : SimulationResult
        The simulation result object.
        
    Example
    -------
    >>> from sphinxsim.llm.mock_llm import MockLLM
    >>> llm = MockLLM()
    >>> config = llm.generate("water flowing at 2 m/s for 5 seconds")
    >>> builder, result = run_from_config(config)
    >>> print(f"Simulation completed: {result.end_time}s")
    """
    import os
    
    # Determine output directory
    if output_dir is None:
        # Use .build-temp for all generated outputs
        project_root = Path(__file__).parent.parent.parent
        safe_name = config.name.replace(' ', '_').replace('/', '_')[:50]
        output_dir = project_root / ".build-temp" / "simulations" / safe_name
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert config to builder
    builder = config_to_builder(config, output_dir)
    
    # Run simulation
    original_dir = Path.cwd()
    try:
        if change_dir:
            os.chdir(output_dir)
        
        result = builder.run(end_time=config.time_stepping.end_time)
        
        return builder, result
        
    finally:
        if change_dir:
            os.chdir(original_dir)
