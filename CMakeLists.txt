# CMakeLists.txt for building the Python extension

cmake_minimum_required(VERSION 3.16)
project(sphinxsim_python_binding)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Set vcpkg toolchain file if not already set
if(NOT CMAKE_TOOLCHAIN_FILE)
    # Try different common vcpkg locations
    if(DEFINED ENV{VCPKG_ROOT})
        set(CMAKE_TOOLCHAIN_FILE "$ENV{VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake" 
            CACHE STRING "Vcpkg toolchain file")
    elseif(EXISTS "$ENV{HOME}/vcpkg/scripts/buildsystems/vcpkg.cmake")
        set(CMAKE_TOOLCHAIN_FILE "$ENV{HOME}/vcpkg/scripts/buildsystems/vcpkg.cmake" 
            CACHE STRING "Vcpkg toolchain file")
    elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/vcpkg/scripts/buildsystems/vcpkg.cmake")
        set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_SOURCE_DIR}/vcpkg/scripts/buildsystems/vcpkg.cmake" 
            CACHE STRING "Vcpkg toolchain file")
    endif()
endif()

# Find required packages
# Use the system Python version (3.11)
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

# Option: Build with full SPHinXsys features (recommended for actual simulations)
# This links against sphinxsys_2d and sphinxsys_3d libraries
option(BUILD_SIMPLE_BINDING "Build simple binding without full SPHinXsys" OFF)

if(BUILD_SIMPLE_BINDING)
    # Create a simple Python module for testing
    pybind11_add_module(_sphinxsys_core 
        sphinxsim/bindings/sphinxsys_python.cpp
    )
    
    # Set properties
    target_compile_definitions(_sphinxsys_core PRIVATE VERSION_INFO="0.1.0")
    target_compile_features(_sphinxsys_core PRIVATE cxx_std_17)
    
else()
    # Full SPHinXsys build - build libraries as part of this project
    
    # Set SPHinXsys options to minimal for Python binding
    set(SPHINXSYS_BUILD_TESTS OFF CACHE BOOL "Don't build tests")
    set(SPHINXSYS_DEVELOPER_MODE OFF CACHE BOOL "Disable developer mode") 
    set(SPHINXSYS_USE_SYCL OFF CACHE BOOL "Disable SYCL")
    set(SPHINXSYS_MODULE_OPENCASCADE ON CACHE BOOL "Enable OpenCASCADE")
    set(SPHINXSYS_2D ON CACHE BOOL "Build 2D library")
    set(SPHINXSYS_3D OFF CACHE BOOL "Don't build 3D library to avoid conflicts")
    
    # Include SPHinXsys as subdirectory - this will build the libraries
    add_subdirectory(sphinxsim/sphinxsys)
    
    # Create the Python module
    pybind11_add_module(_sphinxsys_core 
        sphinxsim/bindings/sphinxsys_python.cpp
    )
    
    # Link with only 2D library to avoid Vecd type conflicts
    target_link_libraries(_sphinxsys_core PRIVATE 
        sphinxsys_2d
    )
    
    # The include directories are automatically handled by the targets
    
    # Set properties
    target_compile_definitions(_sphinxsys_core PRIVATE VERSION_INFO="0.1.0")
    target_compile_features(_sphinxsys_core PRIVATE cxx_std_17)
    
endif()

# Install the module where Python can find it
install(TARGETS _sphinxsys_core DESTINATION .)
