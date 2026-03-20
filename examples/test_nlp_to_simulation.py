#!/usr/bin/env python3
"""
End-to-end demonstration: Natural Language → Simulation

This example shows the complete workflow:
1. Natural language input → LLM
2. LLM → SimulationConfig (validated schema)
3. SimulationConfig JSON → SPHSimulation
4. Run simulation and get results
"""

import sys
import os
from pathlib import Path

def find_project_root(start: Path | None = None):
    start = start or Path.cwd()
    for path in [start] + list(start.parents):
        if (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("Project root not found")

PROJECT_ROOT = find_project_root()

# Add parent directory for imports
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "build-integrated"))
original_dir = Path.cwd()

import _sphinxsys_core_2d as sph


def main():
    """Run natural language to simulation demo."""
    
    print("🤖 Natural Language → Simulation Pipeline Demo")
    print("=" * 60)
    
    # Import modules
    try:
        from sphinxsim.llm import get_llm
        from sphinxsim.config.schemas import SimulationConfig
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        print("Make sure you're in the project root and dependencies are installed")
        return False
    
    # Example natural language inputs
    examples = [
        "water dam break simulation for 0.5 seconds",
        "water flowing at 3 m/s through a channel for 1 second",
        "quick flow test for 0.2 s",
    ]
    
    print("\n📝 Available test scenarios:")
    for i, example in enumerate(examples, 1):
        print(f"   {i}. {example}")
    
    # Choose which example to run
    choice = 1  # Default to dam break
    if len(sys.argv) > 1:
        try:
            choice = int(sys.argv[1])
            if choice < 1 or choice > len(examples):
                print(f"⚠️  Invalid choice {choice}, using default (1)")
                choice = 1
        except ValueError:
            print(f"⚠️  Invalid input, using default (1)")
    
    description = examples[choice - 1]
    print(f"\n🎯 Selected: \"{description}\"")
    print()
    
    # Step 1: Natural Language → Config
    print("=" * 60)
    print("Step 1: Natural Language → SimulationConfig")
    print("=" * 60)
    
    llm = get_llm()
    config = llm.generate(description)
    
    print(f"✅ Generated configuration:")
    print(f"   Domain dimensions: {config.domain.dimensions}")
    print(f"   Particle spacing: {config.particle_spacing}")
    print(f"   Particle boundary buffer: {config.particle_boundary_buffer}")
    print(f"   Fluid blocks: {len(config.fluid_blocks)}")
    for block in config.fluid_blocks:
        print(f"     - {block.name}: dims={block.dimensions}, rho={block.density}, c={block.sound_speed}")
    print(f"   Walls: {len(config.walls)}")
    for wall in config.walls:
        print(
            f"     - {wall.name}: width={config.particle_boundary_buffer * config.particle_spacing}"
        )
    if config.gravity is not None:
        print(f"   Gravity: {config.gravity}")
    print(f"   Observers: {len(config.observers)}")
    print(f"   End time: {config.end_time if config.end_time is not None else '(set at runtime)'}")
    
    # Validate config can round-trip through JSON
    config_json = config.model_dump_json(indent=2)
    print(f"\n📄 Configuration as JSON ({len(config_json)} bytes)")
    print(config_json[:200] + "..." if len(config_json) > 200 else config_json)
    
    # Save config to JSON file
    import json
    config_file = PROJECT_ROOT / ".build-temp" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as f:
        f.write(config_json)
    print(f"✅ Config saved to: {config_file}")
    
    # Test loading from JSON file (round-trip validation)
    with open(config_file, "r") as f:
        loaded_json = f.read()
    config_reloaded = SimulationConfig.model_validate_json(loaded_json)
    print("✅ Config successfully loaded and validated from JSON file")
    
    # Step 2: Config → Executable Simulation
    print("\n" + "=" * 60)
    print("Step 2: SimulationConfig JSON → SPHSimulation")
    print("=" * 60)
    
    try:
        sim = sph.SPHSimulation(str(config_file))
        sim.loadConfig()
        print("✅ Simulation created and configuration loaded")
        
        # Create temp directory in project root, not relative to cwd
        work_dir = PROJECT_ROOT / ".build-temp" / "test_simulation"
        work_dir.mkdir(exist_ok=True, parents=True)
        os.chdir(work_dir)
        sim.run(config_reloaded.end_time if config_reloaded.end_time is not None else 1.0)
        
        print("✅ Simulation completed successfully!")
        print(f"\n📊 Summary:")
        print(f"   End time: {config_reloaded.end_time if config_reloaded.end_time is not None else 1.0}s")
        print(f"   Fluid block: {config_reloaded.fluid_blocks[0].name}")
        print(f"   Domain dimensions: {config_reloaded.domain.dimensions}")
        
        # Show output location
        safe_name = config_reloaded.fluid_blocks[0].name.replace(' ', '_').replace('/', '_')[:50]
        output_dir = PROJECT_ROOT / ".build-temp" / "simulations" / safe_name
        print(f"\n📁 Simulation output saved to:")
        print(f"   {output_dir}")
        
        return True
        
    except RuntimeError as e:
        if "C++ extension" in str(e):
            print("❌ C++ extension not available")
            print("\n🔧 Please build the C++ extension:")
            print("   cd sphinxsim/sphinxsys")
            print("   cmake --preset integrated-build")
            print("   ninja -C ../../build-integrated")
            return False
        else:
            raise
    
    except NotImplementedError as e:
        print(f"❌ Feature not yet implemented: {e}")
        print("\n💡 Tip: Try a fluid-only simulation like:")
        print('   "water dam break for 1 second"')
        return False
    
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original directory
        os.chdir(original_dir)

def test_nlp_to_simulation():
    assert main()

if __name__ == "__main__":
    print()
    success = main()
    print()
    if success:
        print("🎉 Demo completed successfully!")
        print("=" * 60)
    else:
        print("❌ Demo encountered errors")
        print("=" * 60)
    
    sys.exit(0 if success else 1)
