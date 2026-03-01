/**
 * @file sphinxsys_python.cpp
 * @brief Python bindings for SPHinXsys using pybind11
 * @details This file creates the _sphinxsys_core module that bridges
 *          Python and the SPHinXsys C++ library
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/eigen.h>  // For Eigen matrix/vector support
#include <string>
#include <memory>

// Include SPHinXsys headers
#include "sph_simulation.h"  // High-level user API
#include "sphinxsys.h"       // Core SPH functionality

namespace py = pybind11;
using namespace SPH;

/**
 * @brief Create the _sphinxsys_core Python module
 */
PYBIND11_MODULE(_sphinxsys_core, m) {
    m.doc() = "SPHinXsys Python bindings - High-level simulation API";
    
    // Bind the main SPHSimulation class
    py::class_<SPHSimulation>(m, "SPHSimulation")
        .def(py::init<>(), "Create a new SPH simulation")
        .def("createDomain", &SPHSimulation::createDomain,
             "Set the domain dimensions and reference particle spacing",
             py::arg("domain_dimensions"), py::arg("particle_spacing"))
        .def("addFluidBlock", &SPHSimulation::addFluidBlock,
             "Add a named fluid block and return its builder for configuration",
             py::arg("name"),
             py::return_value_policy::reference)
        .def("addWall", &SPHSimulation::addWall,
             "Add a named wall and return its builder for configuration", 
             py::arg("name"),
             py::return_value_policy::reference)
        .def("enableGravity", &SPHSimulation::enableGravity,
             "Enable uniform gravitational acceleration",
             py::arg("gravity"))
        .def("addObserver", py::overload_cast<const std::string&, const Vecd&>
             (&SPHSimulation::addObserver),
             "Add a single-point observer at the given position",
             py::arg("name"), py::arg("position"))
        .def("addObserver", py::overload_cast<const std::string&, const StdVec<Vecd>&>
             (&SPHSimulation::addObserver),
             "Add a multi-point observer at the given positions",
             py::arg("name"), py::arg("positions"))
        .def("useSolver", &SPHSimulation::useSolver,
             "Return the solver configuration object for fluent setup",
             py::return_value_policy::reference)
        .def("run", &SPHSimulation::run,
             "Build all SPH objects and run the simulation until end_time",
             py::arg("end_time"));
    
    // Bind the FluidBlockBuilder class
    py::class_<FluidBlockBuilder>(m, "FluidBlockBuilder")
        .def("block", &FluidBlockBuilder::block,
             "Define the fluid block dimensions (starting at coordinate origin)",
             py::arg("dimensions"),
             py::return_value_policy::reference)
        .def("material", &FluidBlockBuilder::material,
             "Set the weakly-compressible fluid material parameters",
             py::arg("rho0"), py::arg("c"),
             py::return_value_policy::reference);
    
    // Bind the WallBuilder class
    py::class_<WallBuilder>(m, "WallBuilder")
        .def("hollowBox", &WallBuilder::hollowBox,
             "Define the wall as a hollow rectangular box aligned with the origin",
             py::arg("domain_dimensions"), py::arg("wall_width"),
             py::return_value_policy::reference);
    
    // Bind the SolverConfig class
    py::class_<SolverConfig>(m, "SolverConfig")
        .def("dualTimeStepping", &SolverConfig::dualTimeStepping,
             "Enable dual time stepping solver",
             py::return_value_policy::reference)
        .def("freeSurfaceCorrection", &SolverConfig::freeSurfaceCorrection,
             "Enable free surface correction",
             py::return_value_policy::reference);
    
    // Module version info
    m.attr("__version__") = "0.1.0";
}
