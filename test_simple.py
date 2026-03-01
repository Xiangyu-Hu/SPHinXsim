#!/usr/bin/env python3
"""
Simple test for SPHinXsys Python bindings - just test object creation
"""

import sys
import os

# Add the build directory to Python path so we can import our module
sys.path.insert(0, '/home/xyhu/SPHinXsim/build-integrated')

try:
    import _sphinxsys_core as sph
    print("✅ Successfully imported _sphinxsys_core")
    print(f"Module version: {sph.__version__}")
    
    # Test object creation only
    print("\n🧪 Testing object creation...")
    
    # Test SPHSimulation creation
    sim = sph.SPHSimulation()
    print("✅ Created SPHSimulation instance")
    
    # Test builder creation (without calling methods)
    print("📋 Available classes and methods:")
    print(f"  SPHSimulation: {dir(sph.SPHSimulation)}")
    print(f"  FluidBlockBuilder: {dir(sph.FluidBlockBuilder)}")
    print(f"  WallBuilder: {dir(sph.WallBuilder)}")
    print(f"  SolverConfig: {dir(sph.SolverConfig)}")
    
    print("\n🎯 Basic binding test successful!")
    print("💡 Object creation works - SPHinXsys bindings are functional")
    
except Exception as e:
    print(f"❌ Error during testing: {e}")
    import traceback
    traceback.print_exc()
