#include "sph_simulation_builder.h"

#include "sphinxsys.h"

#include <fstream>
#include <stdexcept>

namespace SPH
{
//=================================================================================================//
FluidBlockBuilder::FluidBlockBuilder(const std::string &name) : name_(name) {}
//=================================================================================================//
FluidBlockBuilder &FluidBlockBuilder::block(const Vecd &dimensions)
{
    dimensions_ = dimensions;
    return *this;
}
//=================================================================================================//
FluidBlockBuilder &FluidBlockBuilder::material(Real rho0, Real c)
{
    rho0_ = rho0;
    c_ = c;
    return *this;
}
//=================================================================================================//
WallBuilder::WallBuilder(const std::string &name) : name_(name) {}
//=================================================================================================//
WallBuilder &WallBuilder::hollowBox(const Vecd &domain_dimensions, Real wall_width)
{
    domain_dims_ = domain_dimensions;
    BW_ = wall_width;
    return *this;
}
//=================================================================================================//
SolverConfig &SolverConfig::dualTimeStepping()
{
    dual_time_stepping_ = true;
    return *this;
}
//=================================================================================================//
SolverConfig &SolverConfig::freeSurfaceCorrection()
{
    free_surface_correction_ = true;
    return *this;
}
//=================================================================================================//
} // namespace SPH
