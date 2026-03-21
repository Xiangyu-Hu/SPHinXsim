/**
 * @file sphinxsys_python.cpp
 * @brief Python bindings for SPHinXsys using pybind11
 * @details This file creates the _sphinxsys_core module that bridges
 *          Python and the SPHinXsys C++ library
 */
#include <pybind11/eigen.h> // For Eigen matrix/vector support
#include <pybind11/pybind11.h>

// Include SPHinXsys headers FIRST to ensure proper type definitions
#include "sph_simulation.h" // High-level user API
#include "sphinxsys.h"      // Core SPH functionality - must be first

// Include pybind11 headers AFTER SPHinXsys to avoid type conflicts
#include <pybind11/stl.h>
#include <pybind11/stl/filesystem.h>

namespace py = pybind11;
using namespace SPH;

#ifdef SPHINXSYS_2D
#define MODULE_NAME _sphinxsys_core_2d
#else
#define MODULE_NAME _sphinxsys_core_3d
#endif

PYBIND11_MODULE(MODULE_NAME, m)
{
    m.doc() = "SPHinXsys Python bindings - High-level simulation API";

    // Note: Eigen types (Vec2d/Vec3d) are automatically converted to/from numpy
    // arrays by pybind11/eigen.h, so no manual binding needed for vectors

    // Bind the main SPHSimulation class
    py::class_<SPHSimulation>(m, "SPHSimulation")
        .def(py::init<const std::filesystem::path &>(), py::arg("config_path"),
             "Initialize SPHSimulation with path to JSON config file")
        .def("loadConfig", &SPHSimulation::loadConfig,
             "Build simulation from JSON file specified at initialization")
        .def("initializeSimulation", &SPHSimulation::initializeSimulation,
             "Initialize executable simulation state after build and before run")
        .def("run", &SPHSimulation::run, py::arg("end_time"),
             "Run simulation until end_time (requires initializeSimulation first)");

    // Module version info
    m.attr("__version__") = "0.1.0";
}
