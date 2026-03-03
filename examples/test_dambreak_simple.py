#!/usr/bin/env python3
"""
SPHinXsys Python Dam Break Example
A complete working demonstration of the SPHinXsys Python bindings
Based on the C++ dambreak_chi.cpp example
"""

import sys
import os

# Prevent Python from creating __pycache__ directories
sys.dont_write_bytecode = True

import time
import math
import tempfile
from pathlib import Path

def find_project_root(start: Path | None = None):
    start = start or Path.cwd()
    for path in [start] + list(start.parents):
        if (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("Project root not found")

PROJECT_ROOT = find_project_root()

# Add the build directory to Python path (relative to examples folder)
sys.path.insert(0, str(PROJECT_ROOT / "build-integrated"))

def main(work_dir=None, simulation_time=2.0, use_temp_dir=True):
    """Run the dam break simulation"""

    print("🌊 SPHinXsys Python Dam Break Example")
    print("=" * 45)

    try:
        import _sphinxsys_core as sph
        print("✅ SPHinXsys module imported")
        import numpy as np
                
        # Simulation parameters (from dambreak_chi.cpp)
        DL = 5.366                    # Water tank length
        DH = 5.366                    # Water tank height  
        LL = 2.0                      # Water column length
        LH = 1.0                      # Water column height
        particle_spacing_ref = 0.025  # Initial reference particle spacing
        BW = particle_spacing_ref * 4 # Thickness of tank wall
        
        # Material parameters
        rho0_f = 1.0                       # Reference density of fluid
        gravity_g = 1.0                    # Gravity
        U_ref = 2.0 * math.sqrt(gravity_g * LH)  # Characteristic velocity
        c_f = 10.0 * U_ref                 # Artificial sound speed
        
        print(f"📋 Parameters:")
        print(f"   Domain: {DL}×{DH}")
        print(f"   Water column: {LL}×{LH}")
        print(f"   Particle spacing: {particle_spacing_ref}")
        print(f"   Material: ρ₀={rho0_f}, c={c_f:.1f}")
        
        # Create and configure simulation
        sim = sph.SPHSimulation()
        
        # Domain setup
        v = np.array([1.0, 2.0])
        sim.createDomain(v, particle_spacing_ref)
        print("✅ Domain created")
        
        # Fluid block setup
        fluid = sim.addFluidBlock("WaterBody")
        fluid.block([LL, LH]).material(rho0_f, c_f)
        print("✅ Fluid block configured")
        
        # Wall boundary setup
        wall = sim.addWall("WallBoundary")
        wall.hollowBox([DL, DH], BW)
        print("✅ Wall boundary configured")
        
        # Physics setup
        sim.enableGravity([0.0, -gravity_g])
        print("✅ Gravity enabled")
        
        # Observer for monitoring
        sim.addObserver("FluidObserver", [DL, 0.2])
        print("✅ Observer added")
        
        # Solver configuration
        solver = sim.useSolver()
        solver.dualTimeStepping().freeSurfaceCorrection()
        print("✅ Solver configured")
        
        if work_dir is None and use_temp_dir:
            # Create temp directory in project root, not relative to cwd
            work_dir = PROJECT_ROOT / ".build-temp" / "dam_break_example"
            work_dir.mkdir(exist_ok=True, parents=True)
        if work_dir is not None:
            os.chdir(work_dir)

        print(f"📁 Now, the work folder is changed to: {work_dir}")

        # Run simulation
        print("\n🚀 Running simulation...")
        
        start_time = time.time()
        sim.run(simulation_time)
        elapsed_time = time.time() - start_time
        
        print(f"\n🎉 Simulation completed!")
        print(f"   Physical time: {simulation_time}s")
        print(f"   Wall clock time: {elapsed_time:.2f}s")
        print(f"   Performance: {simulation_time/elapsed_time:.3f}x real-time")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        print("Make sure the module was built successfully")
        return False
    except Exception as e:
        print(f"❌ Simulation failed: {e}")
        return False

def test_example_dambreak_simple(tmp_path):
    assert main()

if __name__ == "__main__":

    success = main()
    
    if success:
        print("\n✅ Dam break example completed successfully!")
        print("🎯 SPHinXsys Python bindings are fully functional")
    else:
        print("\n❌ Example failed")
    
    print("=" * 45)
