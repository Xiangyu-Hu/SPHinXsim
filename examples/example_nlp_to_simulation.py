#!/usr/bin/env python3
"""
End-to-end demonstration: Natural Language → Simulation

This example shows the complete workflow:
1. Natural language input → LLM
2. LLM → SimulationConfig (validated schema)
3. SimulationConfig → SimulationBuilder (executable)
4. Run simulation and get results
"""

import sys
from pathlib import Path

# Prevent Python bytecode generation
sys.dont_write_bytecode = True

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "build-integrated"))


def main():
    """Run natural language to simulation demo."""
    
    print("🤖 Natural Language → Simulation Pipeline Demo")
    print("=" * 60)
    
    # Import modules
    try:
        from sphinxsim.llm import MockLLM, run_from_config
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
    
    llm = MockLLM()
    config = llm.generate(description)
    
    print(f"✅ Generated configuration:")
    print(f"   Name: {config.name}")
    print(f"   Physics: {config.physics.value}")
    print(f"   Domain: {config.domain.bounds_min} → {config.domain.bounds_max}")
    print(f"   Resolution: {config.domain.resolution}")
    print(f"   Materials: {len(config.materials)} material(s)")
    for mat in config.materials:
        print(f"     - {mat.name}: ρ={mat.density} kg/m³")
    print(f"   Boundary conditions: {len(config.boundary_conditions)}")
    for bc in config.boundary_conditions:
        print(f"     - {bc.name} ({bc.type.value})")
    print(f"   End time: {config.time_stepping.end_time}s")
    print(f"   Output interval: {config.time_stepping.output_interval}s")
    
    # Validate config can round-trip through JSON
    config_json = config.model_dump_json(indent=2)
    print(f"\n📄 Configuration as JSON ({len(config_json)} bytes)")
    print(config_json[:200] + "..." if len(config_json) > 200 else config_json)
    
    # Step 2: Config → Executable Simulation
    print("\n" + "=" * 60)
    print("Step 2: SimulationConfig → Executable Simulation")
    print("=" * 60)
    
    try:
        builder, result = run_from_config(config)
        
        print("✅ Simulation completed successfully!")
        print(f"\n📊 Results:")
        print(f"   End time: {result.end_time}s")
        print(f"   Fluid blocks: {len(result.fluid_blocks)}")
        for fluid in result.fluid_blocks:
            print(f"     - {fluid}")
        print(f"   Wall boundaries: {len(result.walls)}")
        for wall in result.walls:
            print(f"     - {wall}")
        if result.gravity:
            print(f"   Gravity: {result.gravity}")
        print(f"   Observers: {len(result.observers)}")
        for name, pos in result.observers:
            print(f"     - {name} at position {pos}")
        
        # Show output location
        project_root = Path(__file__).parent.parent
        safe_name = config.name.replace(' ', '_').replace('/', '_')[:50]
        output_dir = project_root / ".build-temp" / "simulations" / safe_name
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
