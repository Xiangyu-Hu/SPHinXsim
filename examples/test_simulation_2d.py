#!/usr/bin/env python3
"""
SPHinXsys Python Dam Break Example
A complete working demonstration of the SPHinXsys Python bindings
Based on the C++ dambreak_chi.cpp example
"""

import sys
import os
import time
import math
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
original_dir = Path.cwd()

def main(work_dir=None, simulation_time=2.0, use_temp_dir=True):
    """Run the dam break simulation"""

    print("🌊 SPHinXsys Python Dam Break Example")
    print("=" * 45)

    try:
        import _sphinxsys_core_2d as sph
        print("✅ SPHinXsys module imported")
        import numpy as np
                
        # Use an absolute path so tests are independent of current working directory.
        config_path = PROJECT_ROOT / "examples" / "input" / "test_simulation_2d" / "config.json"
        sim = sph.SPHSimulation(str(config_path))
        sim.loadConfig()
        print("✅ Simulation configuration loaded")

        if work_dir is None and use_temp_dir:
            # Create temp directory in project root, not relative to cwd
            work_dir = PROJECT_ROOT / ".build-temp" / "test_simulation_2d"
            work_dir.mkdir(exist_ok=True, parents=True)
        if work_dir is not None:
            os.chdir(work_dir)

        print(f"📁 Now, the work folder is changed to: {work_dir}")

        sim.initializeSimulation()
        print("✅ Simulation initialized")

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
    finally:
        # Restore original directory
        os.chdir(original_dir)

def test_example_dambreak_2d():
    basetemp_path = PROJECT_ROOT / ".build-temp" / ".pytest_tmp"
    basetemp_path.mkdir(parents=True, exist_ok=True)
    assert main(basetemp_path)

if __name__ == "__main__":

    success = main()
    
    if success:
        print("\n✅ Dam break example completed successfully!")
        print("🎯 SPHinXsys Python bindings are fully functional")
    else:
        print("\n❌ Example failed")
    
    print("=" * 45)
