#!/usr/bin/env python3
"""
Example demonstrating the high-level SimulationBuilder API.

This example shows how to set up and run a dam break simulation
using the fluent builder interface.
"""

import sys
from pathlib import Path

# Prevent Python bytecode generation
sys.dont_write_bytecode = True

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Add build directory for C++ bindings
sys.path.insert(0, str(Path(__file__).parent.parent / "build-integrated"))

from sphinxsim.bindings import SimulationBuilder


def main():
    """Run a dam break simulation using SimulationBuilder."""
    
    print("🌊 Dam Break Simulation using SimulationBuilder")
    print("=" * 50)
    
    import os
    original_dir = Path.cwd()
    
    try:
        # Output directory in .build-temp (relative to project root)
        project_root = Path(__file__).parent.parent
        output_dir = project_root / ".build-temp" / "dambreak_output"
        output_dir.mkdir(exist_ok=True, parents=True)
        
        # Change to output directory for simulation
        os.chdir(output_dir)
        
        # Create a simulation builder with fluent interface
        builder = (
            SimulationBuilder(
                domain_size=[5.366, 5.366],
                particle_spacing=0.025,
                output_prefix="dambreak"
            )
            # Add water fluid block
            .add_fluid_block(
                name="WaterBody",
                block_size=[2.0, 1.0],
                density=1.0,
                sound_speed=20.0
            )
            # Add tank walls
            .add_wall_boundary(
                name="WallBoundary",
                domain_size=[5.366, 5.366],
                wall_thickness=0.1
            )
            # Set gravity
            .set_gravity([0.0, -1.0])
            # Add observer for monitoring
            .add_observer(name="FluidObserver", position=[5.366, 0.2])
            # Configure solver
            .enable_dual_time_stepping()
            .enable_free_surface_correction()
        )
        
        print("✅ Simulation configured:")
        print(builder)
        print()
        
        # Run the simulation
        print("🚀 Running simulation...")
        result = builder.run(end_time=0.5)
        
        print("\n🎉 Simulation completed!")
        print(f"   End time: {result.end_time}s")
        print(f"   Fluid blocks: {len(result.fluid_blocks)}")
        for fluid in result.fluid_blocks:
            print(f"     - {fluid}")
        print(f"   Wall boundaries: {len(result.walls)}")
        for wall in result.walls:
            print(f"     - {wall}")
        print(f"   Gravity: {result.gravity}")
        print(f"   Observers: {len(result.observers)}")
        for name, pos in result.observers:
            print(f"     - {name} at {pos}")
        
        return True
        
    except RuntimeError as e:
        print(f"❌ Error: {e}")
        print("\nPlease rebuild the C++ extension:")
        print("  cd sphinxsim/sphinxsys && ninja -C ../../build-integrated")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original directory
        os.chdir(original_dir)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
