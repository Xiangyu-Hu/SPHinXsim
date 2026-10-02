# CMakeLists.txt for building the Python extension

cmake_minimum_required(VERSION 3.16)
project(sphinxsim_python_binding)

include(CTest)
option(SPHINXSIM_BUILD_SIMULATION_TESTS "Build and register simulation class C++ tests" ON)
option(SPHINXSIM_COPY_BINDINGS_AFTER_BUILD "Copy Python bindings to a stable folder after build" OFF)
option(SPHINXSIM_BUILD_PROJECT "Build with extra SPHinXsim Project" ON)
set(SPHINXSIM_BINDINGS_OUTPUT_DIR "${CMAKE_SOURCE_DIR}/sphinxsim/bindings/native"
    CACHE PATH "Stable output directory for copied Python binding modules")

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
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
find_package(pybind11 CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)

# Install extension modules into the package so wheel installers place them
# directly under site-packages/sphinxsim/bindings/native.
set(SPHINXSIM_PYTHON_INSTALL_DIR "sphinxsim/bindings/native"
    CACHE PATH "Install destination for Python extension modules")

if(SPHINXSIM_BUILD_SIMULATION_TESTS)
    if(DEFINED VCPKG_INSTALLED_DIR AND DEFINED VCPKG_TARGET_TRIPLET)
        set(_SPHINXSIM_VCPKG_GTEST_DIR "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/share/gtest")
        if(EXISTS "${_SPHINXSIM_VCPKG_GTEST_DIR}/GTestConfig.cmake")
            set(GTest_DIR "${_SPHINXSIM_VCPKG_GTEST_DIR}" CACHE PATH "Path to GTestConfig.cmake" FORCE)
            find_package(GTest CONFIG REQUIRED
                PATHS "${_SPHINXSIM_VCPKG_GTEST_DIR}"
                NO_DEFAULT_PATH)
        else()
            find_package(GTest CONFIG REQUIRED)
        endif()
    else()
        find_package(GTest CONFIG REQUIRED)
    endif()
    include(GoogleTest)
endif()

# Set SPHinXsys options to minimal for Python binding
set(SPHINXSYS_BUILD_TESTS OFF CACHE BOOL "Don't build tests")
set(SPHINXSYS_DEVELOPER_MODE OFF CACHE BOOL "Disable developer mode") 
set(SPHINXSYS_USE_SYCL OFF CACHE BOOL "Disable SYCL")
set(SPHINXSYS_2D ON CACHE BOOL "Build 2D library")
set(SPHINXSYS_3D ON CACHE BOOL "Build 3D library")

function(get_all_include_dirs result dir)
    file(GLOB_RECURSE children LIST_DIRECTORIES true "${dir}/*")

    set(dirs "${dir}")
    foreach(child ${children})
        if(IS_DIRECTORY "${child}")
            list(APPEND dirs "${child}")
        endif()
    endforeach()

    list(REMOVE_DUPLICATES dirs)
    set(${result} "${dirs}" PARENT_SCOPE)
endfunction()

# Include headers and sources for sph simulation library
file(GLOB_RECURSE SPH_SIMULATION_INCLUDE CONFIGURE_DEPENDS sphinxsim/sph_simulation/*.h sphinxsim/sph_simulation/*.hpp)
file(GLOB_RECURSE SPH_SIMULATION_SOURCES CONFIGURE_DEPENDS sphinxsim/sph_simulation/*.cpp)
get_all_include_dirs(SPH_SIMULATION_INCLUDE "sphinxsim/sph_simulation")

# Include SPHinXsys as subdirectory - this will build the sphinxsys libraries
add_subdirectory(sphinxsim/sphinxsys)

if(SPHINXSIM_BUILD_PROJECT) # Include extra headers and sources for SPHinXsim project
    file(GLOB_RECURSE EXTRA_PROJECT_INCLUDE CONFIGURE_DEPENDS project/extra_src/*.h project/extra_src/*.hpp)
    file(GLOB_RECURSE EXTRA_PROJECT_SOURCES CONFIGURE_DEPENDS project/extra_src/*.cpp)
    get_all_include_dirs(EXTRA_PROJECT_INCLUDE "project/extra_src")
    set(SPHINXSIM_PROJECT ON CACHE BOOL "Build with extra SPHinXsim Project")
else()
    set(SPHINXSIM_PROJECT OFF CACHE BOOL "Don't build with extra SPHinXsim Project")
endif()

# Define the source file
set(BIND_SOURCE_FILES sphinxsim/bindings/sphinxsys_python.cpp)

if(SPHINXSYS_2D)
    add_library(sph_simulation_2d)
    set_target_properties(sph_simulation_2d PROPERTIES POSITION_INDEPENDENT_CODE ON)
    
    if(SPHINXSIM_BUILD_PROJECT)
        target_include_directories(sph_simulation_2d PUBLIC ${SPH_SIMULATION_INCLUDE} ${EXTRA_PROJECT_INCLUDE})
        target_sources(sph_simulation_2d PRIVATE ${SPH_SIMULATION_SOURCES} ${EXTRA_PROJECT_SOURCES})
        target_compile_definitions(sph_simulation_2d PUBLIC SPHINXSYS_2D SPHINXSIM_PROJECT)
    else()
        target_include_directories(sph_simulation_2d PUBLIC ${SPH_SIMULATION_INCLUDE} ${EXTRA_PROJECT_INCLUDE})
        target_sources(sph_simulation_2d PRIVATE ${SPH_SIMULATION_SOURCES} ${EXTRA_PROJECT_SOURCES})
    target_compile_definitions(sph_simulation_2d PUBLIC SPHINXSYS_2D)
        endif()

    target_link_libraries(sph_simulation_2d PRIVATE nlohmann_json::nlohmann_json)
    target_link_libraries(sph_simulation_2d PUBLIC sphinxsys_2d)
    target_compile_definitions(sph_simulation_2d PUBLIC VERSION_INFO="0.1.0")
    
    pybind11_add_module(_sphinxsys_core_2d ${BIND_SOURCE_FILES})
    target_link_libraries(_sphinxsys_core_2d PRIVATE sph_simulation_2d)
    target_compile_features(_sphinxsys_core_2d PRIVATE cxx_std_17)
    install(TARGETS _sphinxsys_core_2d DESTINATION ${SPHINXSIM_PYTHON_INSTALL_DIR})

    if(SPHINXSIM_COPY_BINDINGS_AFTER_BUILD)
        add_custom_command(TARGET _sphinxsys_core_2d POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E make_directory ${SPHINXSIM_BINDINGS_OUTPUT_DIR}
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                $<TARGET_FILE:_sphinxsys_core_2d>
                ${SPHINXSIM_BINDINGS_OUTPUT_DIR}/
            COMMENT "Copying _sphinxsys_core_2d to ${SPHINXSIM_BINDINGS_OUTPUT_DIR}")
    endif()

endif()

if(SPHINXSYS_3D)
    add_library(sph_simulation_3d)
    set_target_properties(sph_simulation_3d PROPERTIES POSITION_INDEPENDENT_CODE ON)

    if(SPHINXSIM_BUILD_PROJECT)
        target_include_directories(sph_simulation_3d PUBLIC ${SPH_SIMULATION_INCLUDE} ${EXTRA_PROJECT_INCLUDE})
        target_sources(sph_simulation_3d PRIVATE ${SPH_SIMULATION_SOURCES} ${EXTRA_PROJECT_SOURCES})
        target_compile_definitions(sph_simulation_3d PUBLIC SPHINXSYS_3D SPHINXSIM_PROJECT)
    else()
        target_include_directories(sph_simulation_3d PUBLIC ${SPH_SIMULATION_INCLUDE} ${EXTRA_PROJECT_INCLUDE})
        target_sources(sph_simulation_3d PRIVATE ${SPH_SIMULATION_SOURCES} ${EXTRA_PROJECT_SOURCES})
    target_compile_definitions(sph_simulation_3d PUBLIC SPHINXSYS_3D)
        endif()    

    target_link_libraries(sph_simulation_3d PRIVATE nlohmann_json::nlohmann_json)
    target_link_libraries(sph_simulation_3d PUBLIC sphinxsys_3d)
    target_compile_definitions(sph_simulation_3d PUBLIC VERSION_INFO="0.1.0")

    pybind11_add_module(_sphinxsys_core_3d ${BIND_SOURCE_FILES})
    target_link_libraries(_sphinxsys_core_3d PRIVATE sph_simulation_3d)
    target_compile_features(_sphinxsys_core_3d PRIVATE cxx_std_17)
    install(TARGETS _sphinxsys_core_3d DESTINATION ${SPHINXSIM_PYTHON_INSTALL_DIR})

    if(SPHINXSIM_COPY_BINDINGS_AFTER_BUILD)
        add_custom_command(TARGET _sphinxsys_core_3d POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E make_directory ${SPHINXSIM_BINDINGS_OUTPUT_DIR}
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                $<TARGET_FILE:_sphinxsys_core_3d>
                ${SPHINXSIM_BINDINGS_OUTPUT_DIR}/
            COMMENT "Copying _sphinxsys_core_3d to ${SPHINXSIM_BINDINGS_OUTPUT_DIR}")
    endif()

endif()

if(SPHINXSIM_BUILD_SIMULATION_TESTS)
    add_subdirectory(tests/test_simulation)
endif()
