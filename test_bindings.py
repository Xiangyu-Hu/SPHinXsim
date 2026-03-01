#!/usr/bin/env python3
"""
Test script for SPHinXsys Python bindings
Reproduces the dambreak example from the C++ code
"""

import sys
import os

# Add the build directory to Python path so we can import our module
sys.path.insert(0, '/home/xyhu/SPHinXsim/build-integrated')

try:
    import _sphinxsys_core as sph
    print("✅ Successfully imported _sphinxsys_core")
    print(f"Module version: {sph.__version__}")
    
    # Test basic functionality - create a simulation like the dambreak example
    print("\n🧪 Testing SPHSimulation...")
    
    # Geometry parameters from dambreak_chi.cpp
    DL = 5.366    # Water tank length
    DH = 5.366    # Water tank height  
    LL = 2.0      # Water column length
    LH = 1.0      # Water column height
    particle_spacing_ref = 0.025
    BW = particle_spacing_ref * 4  # Tank wall thickness
    
    # Material parameters
    rho0_f = 1.0       # Reference density of fluid
    gravity_g = 1.0    # Gravity
    U_ref = 2.0 * (gravity_g * LH)**0.5  # Characteristic velocity
    c_f = 10.0 * U_ref  # Artificial sound speed
    
    # Create simulation
    sim = sph.SPHSimulation()
    print("✅ Created SPHSimulation instance")
    
    # Test with numpy array if available, otherwise skip vector operations
    try:
        import numpy as np
        domain_dims = np.array([DL, DH])
        fluid_dims = np.array([LL, LH])
        wall_dims = np.array([DL, DH]) 
        gravity_vec = np.array([0.0, -gravity_g])
        observer_pos = np.array([DL, 0.2])
        print("✅ Using numpy arrays for vectors")
    except ImportError:
        print("⚠️  NumPy not available - testing without vector operations")
        print("   Note: SPHinXsys requires NumPy for vector operations in Python")
        print("   Install NumPy with: pip install numpy")
        print("✅ Basic import and object creation successful!")
        exit(0)
    
    # Set up domain
    sim.createDomain(domain_dims, particle_spacing_ref)
    print(f"✅ Created domain: {DL}x{DH} with spacing {particle_spacing_ref}")
    
    # Add fluid block
    fluid_builder = sim.addFluidBlock("WaterBody")
    fluid_builder.block(fluid_dims).material(rho0_f, c_f)
    print(f"✅ Added fluid block: {LL}x{LH} with ρ₀={rho0_f}, c={c_f}")
    
    # Add wall
    wall_builder = sim.addWall("WallBoundary")
    wall_builder.hollowBox(wall_dims, BW)
    print(f"✅ Added wall boundary: {DL}x{DH} with thickness {BW}")
    
    # Enable gravity
    sim.enableGravity(gravity_vec)
    print(f"✅ Enabled gravity: [0.0, {-gravity_g}]")
    
    # Add observer
    sim.addObserver("FluidObserver", observer_pos)
    print(f"✅ Added observer at [{DL}, 0.2]")
    
    # Configure solver
    solver = sim.useSolver()
    solver.dualTimeStepping().freeSurfaceCorrection()
    print("✅ Configured solver with dual time stepping and free surface correction")
    
    print("\n🎯 All setup completed successfully!")
    print("💡 Ready to run simulation with: sim.run(20.0)")
    print("   (Not running actual simulation in this test)")
    
except ImportError as e:
    print(f"❌ Failed to import _sphinxsys_core: {e}")
    print("Make sure the module was built successfully")
except Exception as e:
    print(f"❌ Error during testing: {e}")
    import traceback
    traceback.print_exc()
