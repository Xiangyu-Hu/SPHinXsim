# CMakeLists.txt for building the Python extension

cmake_minimum_required(VERSION 3.16)
project(sphinxsim_python_binding)

include(CTest)
option(SPHINXSIM_BUILD_SIMULATION_TESTS "Build and register simulation class C++ tests" ON)

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
find_package(VTK REQUIRED COMPONENTS CommonCore CommonDataModel IOXML)
find_package(nlohmann_json CONFIG REQUIRED)

find_package(GTest CONFIG REQUIRED)
include(GoogleTest)

# Set SPHinXsys options to minimal for Python binding
set(SPHINXSYS_BUILD_TESTS OFF CACHE BOOL "Don't build tests")
set(SPHINXSYS_DEVELOPER_MODE OFF CACHE BOOL "Disable developer mode") 
set(SPHINXSYS_USE_SYCL OFF CACHE BOOL "Disable SYCL")
set(SPHINXSYS_2D ON CACHE BOOL "Build 2D library")
set(SPHINXSYS_3D ON CACHE BOOL "Build 3D library")
    
# Include SPHinXsys as subdirectory - this will build the sphinxsys libraries
add_subdirectory(sphinxsim/sphinxsys)

# Include headers and sources for sph simulation library
set(SPH_SIMULATION_INCLUDE ${CMAKE_SOURCE_DIR}/sphinxsim/sph_simulation)
file(GLOB_RECURSE SPH_SIMULATION_SOURCES CONFIGURE_DEPENDS sphinxsim/sph_simulation/*.cpp)

# Define the source file
set(BIND_SOURCE_FILES sphinxsim/bindings/sphinxsys_python.cpp)

if(SPHINXSYS_2D)
    add_library(sph_simulation_2d)
    target_include_directories(sph_simulation_2d PUBLIC ${SPH_SIMULATION_INCLUDE})
    target_sources(sph_simulation_2d INTERFACE ${SPH_SIMULATION_SOURCES})
    target_link_libraries(sph_simulation_2d INTERFACE VTK::CommonCore VTK::CommonDataModel VTK::IOXML)
    target_link_libraries(sph_simulation_2d PRIVATE nlohmann_json::nlohmann_json)
    target_link_libraries(sph_simulation_2d PUBLIC sphinxsys_2d)
    target_link_libraries(sph_simulation_2d INTERFACE GTest::gtest GTest::gtest_main)

    pybind11_add_module(_sphinxsys_core_2d ${BIND_SOURCE_FILES})
    target_link_libraries(_sphinxsys_core_2d PRIVATE sph_simulation_2d)
    target_compile_definitions(_sphinxsys_core_2d PRIVATE SPHINXSYS_2D VERSION_INFO="0.1.0")
    target_compile_features(_sphinxsys_core_2d PRIVATE cxx_std_17)
    install(TARGETS _sphinxsys_core_2d  DESTINATION .)

    if(SPHINXSIM_BUILD_SIMULATION_TESTS)
        add_subdirectory(tests/test_simulation/test_2d_simulation)
    endif()
endif()

if(SPHINXSYS_3D)
    add_library(sph_simulation_3d)
    target_include_directories(sph_simulation_3d PUBLIC ${SPH_SIMULATION_INCLUDE})
    target_sources(sph_simulation_3d INTERFACE ${SPH_SIMULATION_SOURCES})
    target_link_libraries(sph_simulation_3d INTERFACE VTK::CommonCore VTK::CommonDataModel VTK::IOXML)
    target_link_libraries(sph_simulation_3d PRIVATE nlohmann_json::nlohmann_json)
    target_link_libraries(sph_simulation_3d PUBLIC sphinxsys_3d)
        target_link_libraries(sph_simulation_3d INTERFACE GTest::gtest GTest::gtest_main)

    pybind11_add_module(_sphinxsys_core_3d ${BIND_SOURCE_FILES})
    target_link_libraries(_sphinxsys_core_3d PRIVATE sph_simulation_3d)
    target_compile_definitions(_sphinxsys_core_3d PRIVATE SPHINXSYS_3D VERSION_INFO="0.1.0")
    target_compile_features(_sphinxsys_core_3d PRIVATE cxx_std_17)
    install(TARGETS _sphinxsys_core_3d  DESTINATION .)

    if(SPHINXSIM_BUILD_SIMULATION_TESTS)
        add_subdirectory(tests/test_simulation/test_3d_simulation)
    endif()
endif()
