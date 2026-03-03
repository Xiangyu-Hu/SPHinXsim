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

namespace py = pybind11;
using namespace SPH;
using VecdRef = Eigen::Ref<const Vecd>;

// Helper functions to safely convert Python lists/arrays to Eigen vectors
Vecd convert_to_vecd(const py::object &input)
{
     Vecd result = Vecd::Zero();
     if (py::isinstance<py::array>(input))
     {
          // Handle numpy arrays
          auto arr = input.cast<py::array_t<double>>();
          if (arr.size() != Dimensions)
          {
               throw py::value_error(
                   "Vector must have exactly " + std::to_string(Dimensions) + " elements!");
          }
          auto buf = arr.unchecked<1>();
          for (size_t i = 0; i < Dimensions; ++i)
          {
               result[i] = buf(i);
          }
     }
     return result;
}

void addObserver_single(SPHSimulation &sim, const std::string &name,
                        VecdRef position)
{
     sim.addObserver(name, position);
}

#ifdef SPHINXSYS_2D
#define MODULE_NAME _sphinxsim_2d
#else
#define MODULE_NAME _sphinxsim_3d
#endif

PYBIND11_MODULE(MODULE_NAME, m)
{
     m.doc() = "SPHinXsys Python bindings - High-level simulation API";

     // Note: Eigen types (Vec2d/Vec3d) are automatically converted to/from numpy
     // arrays by pybind11/eigen.h, so no manual binding needed for vectors

     // Bind the main SPHSimulation class
     py::class_<SPHSimulation>(m, "SPHSimulation")
         .def(py::init<>())
         .def("defineDomain", &SPHSimulation::defineDomain,
              py::arg("domain_dimensions"), py::arg("particle_spacing"),
              "Set domain dimensions and reference particle spacing")
         .def("createDomain", &SPHSimulation::createDomain,
              py::arg("domain_dimensions"), py::arg("particle_spacing"),
              "Set domain dimensions and reference particle spacing")
         .def("addFluidBlock", &SPHSimulation::addFluidBlock,
              py::return_value_policy::reference, py::arg("name"),
              "Add a named fluid block; configure with returned builder")
         .def("addWall", &SPHSimulation::addWall,
              py::return_value_policy::reference, py::arg("name"),
              "Add a named solid wall; configure with returned builder")
         .def("enableGravity", &SPHSimulation::enableGravity, py::arg("gravity"),
              "Enable uniform gravitational acceleration")
         .def("addObserver", addObserver_single, py::arg("name"),
              py::arg("position"),
              "Add a single-point observer at the given position")
         .def("useSolver", &SPHSimulation::useSolver,
              py::return_value_policy::reference,
              "Return the solver configuration object")
         .def("run", &SPHSimulation::run, py::arg("end_time"),
              "Build all SPH objects and run simulation until end_time");

     // Bind the FluidBlockBuilder class
     py::class_<FluidBlockBuilder>(m, "FluidBlockBuilder")
         .def("block", &FluidBlockBuilder::block,
              py::return_value_policy::reference, py::arg("dimensions"),
              "Define fluid block dimensions from coordinate origin")
         .def("material", &FluidBlockBuilder::material,
              py::return_value_policy::reference, py::arg("rho0"), py::arg("c"),
              "Set weakly-compressible fluid material parameters")
         .def("getName", &FluidBlockBuilder::getName, "Get the fluid block name")
         .def("getDimensions", &FluidBlockBuilder::getDimensions,
              py::return_value_policy::reference_internal,
              "Get the fluid block dimensions")
         .def("getRho0", &FluidBlockBuilder::getRho0, "Get the reference density")
         .def("getC", &FluidBlockBuilder::getC, "Get the sound speed");

     // Bind the WallBuilder class
     py::class_<WallBuilder>(m, "WallBuilder")
         .def("hollowBox", &WallBuilder::hollowBox,
              py::return_value_policy::reference, py::arg("domain_dimensions"),
              py::arg("wall_width"),
              "Define wall as hollow rectangular box aligned with origin")
         .def("getName", &WallBuilder::getName, "Get the wall name")
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
