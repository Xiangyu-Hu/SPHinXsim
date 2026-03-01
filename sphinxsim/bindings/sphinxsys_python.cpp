/**
 * @file sphinxsys_python.cpp
 * @brief Python bindings for SPHinXsys using pybind11
 * @details This file creates the _sphinxsys_core module that bridges
 *          Python and the SPHinXsys C++ library
 */

// Include SPHinXsys headers FIRST to ensure proper type definitions
#include "sphinxsys.h"       // Core SPH functionality - must be first
#include "sph_simulation.h"  // High-level user API

// Standard library headers
#include <string>
#include <memory>

// Include pybind11 headers AFTER SPHinXsys to avoid type conflicts
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/eigen.h>  // For Eigen matrix/vector support
#include <pybind11/operators.h>

namespace py = pybind11;
using namespace SPH;

// Helper functions to safely convert Python lists/arrays to Eigen vectors
Vecd convert_to_vecd(const py::object& input) {
    if (py::isinstance<py::array>(input)) {
        // Handle numpy arrays
        auto arr = input.cast<py::array_t<double>>();
        if (arr.size() != 2) {
            throw py::value_error("Vector must have exactly 2 elements for 2D simulation");
        }
        auto buf = arr.unchecked<1>();
        return Vecd(buf(0), buf(1));
    } else if (py::isinstance<py::list>(input)) {
        // Handle Python lists
        auto list = input.cast<py::list>();
        if (list.size() != 2) {
            throw py::value_error("Vector must have exactly 2 elements for 2D simulation");
        }
        return Vecd(list[0].cast<double>(), list[1].cast<double>());
    } else {
        throw py::type_error("Vector input must be a numpy array or Python list");
    }
}

// Safe wrapper functions for vector methods
void safe_createDomain(SPHSimulation& sim, const py::object& domain_dimensions, Real particle_spacing) {
    Vecd dims = convert_to_vecd(domain_dimensions);
    sim.createDomain(dims, particle_spacing);
}

void safe_enableGravity(SPHSimulation& sim, const py::object& gravity) {
    Vecd grav = convert_to_vecd(gravity);
    sim.enableGravity(grav);
}

void safe_addObserver_single(SPHSimulation& sim, const std::string& name, const py::object& position) {
    Vecd pos = convert_to_vecd(position);
    sim.addObserver(name, pos);
}

FluidBlockBuilder& safe_block(FluidBlockBuilder& builder, const py::object& dimensions) {
    Vecd dims = convert_to_vecd(dimensions);
    return builder.block(dims);
}

WallBuilder& safe_hollowBox(WallBuilder& builder, const py::object& domain_dimensions, Real wall_width) {
    Vecd dims = convert_to_vecd(domain_dimensions);
    return builder.hollowBox(dims, wall_width);
}

/**
 * @brief Create the _sphinxsys_core Python module
 */
PYBIND11_MODULE(_sphinxsys_core, m) {
    m.doc() = "SPHinXsys Python bindings - High-level simulation API";
    
    // Note: Eigen types (Vec2d/Vec3d) are automatically converted to/from numpy arrays
    // by pybind11/eigen.h, so no manual binding needed for vectors
    
    // Bind the main SPHSimulation class
    py::class_<SPHSimulation>(m, "SPHSimulation")
        .def(py::init<>())
        .def("createDomain", &safe_createDomain,
             py::arg("domain_dimensions"), py::arg("particle_spacing"),
             "Set domain dimensions and reference particle spacing")
        .def("addFluidBlock", &SPHSimulation::addFluidBlock, 
             py::return_value_policy::reference,
             py::arg("name"),
             "Add a named fluid block; configure with returned builder")
        .def("addWall", &SPHSimulation::addWall, 
             py::return_value_policy::reference,
             py::arg("name"),
             "Add a named solid wall; configure with returned builder")
        .def("enableGravity", &safe_enableGravity,
             py::arg("gravity"),
             "Enable uniform gravitational acceleration")
        .def("addObserver", &safe_addObserver_single,
             py::arg("name"), py::arg("position"),
             "Add a single-point observer at the given position")
        .def("useSolver", &SPHSimulation::useSolver, 
             py::return_value_policy::reference,
             "Return the solver configuration object")
        .def("run", &SPHSimulation::run,
             py::arg("end_time"),
             "Build all SPH objects and run simulation until end_time");
    
    // Bind the FluidBlockBuilder class
    py::class_<FluidBlockBuilder>(m, "FluidBlockBuilder")
        .def("block", &safe_block, 
             py::return_value_policy::reference,
             py::arg("dimensions"),
             "Define fluid block dimensions from coordinate origin")
        .def("material", &FluidBlockBuilder::material, 
             py::return_value_policy::reference,
             py::arg("rho0"), py::arg("c"),
             "Set weakly-compressible fluid material parameters")
        .def("getName", &FluidBlockBuilder::getName,
             "Get the fluid block name")
        .def("getDimensions", &FluidBlockBuilder::getDimensions,
             py::return_value_policy::reference_internal,
             "Get the fluid block dimensions")
        .def("getRho0", &FluidBlockBuilder::getRho0,
             "Get the reference density")
        .def("getC", &FluidBlockBuilder::getC,
             "Get the sound speed");
    
    // Bind the WallBuilder class
    py::class_<WallBuilder>(m, "WallBuilder")
        .def("hollowBox", &safe_hollowBox, 
             py::return_value_policy::reference,
             py::arg("domain_dimensions"), py::arg("wall_width"),
             "Define wall as hollow rectangular box aligned with origin")
        .def("getName", &WallBuilder::getName,
             "Get the wall name")
        .def("getDomainDimensions", &WallBuilder::getDomainDimensions,
             py::return_value_policy::reference_internal,
             "Get the wall domain dimensions")
        .def("getWallWidth", &WallBuilder::getWallWidth,
             "Get the wall thickness");
    
    // Bind the SolverConfig class
    py::class_<SolverConfig>(m, "SolverConfig")
        .def("dualTimeStepping", &SolverConfig::dualTimeStepping, 
             py::return_value_policy::reference,
             "Enable dual time stepping (advection + acoustic sub-stepping)")
        .def("freeSurfaceCorrection", &SolverConfig::freeSurfaceCorrection, 
             py::return_value_policy::reference,
             "Enable density summation with free-surface correction")
        .def("isDualTimeStepping", &SolverConfig::isDualTimeStepping,
             "Check if dual time stepping is enabled")
        .def("isFreeSurfaceCorrection", &SolverConfig::isFreeSurfaceCorrection,
             "Check if free surface correction is enabled");
    
    // Module version info
    m.attr("__version__") = "0.1.0";
}
