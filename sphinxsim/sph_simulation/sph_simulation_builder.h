/* ------------------------------------------------------------------------- *
 *                                SPHinXsys                                  *
 * ------------------------------------------------------------------------- *
 * SPHinXsys (pronunciation: s'finksis) is an acronym from Smoothed Particle *
 * Hydrodynamics for industrial compleX systems. It provides C++ APIs for    *
 * physical accurate simulation and aims to model coupled industrial dynamic *
 * systems including fluid, solid, multi-body dynamics and beyond with SPH   *
 * (smoothed particle hydrodynamics), a meshless computational method using  *
 * particle discretization.                                                  *
 *                                                                           *
 * SPHinXsys is partially funded by German Research Foundation               *
 * (Deutsche Forschungsgemeinschaft) DFG HU1527/6-1, HU1527/10-1,            *
 *  HU1527/12-1 and HU1527/12-4.                                             *
 *                                                                           *
 * Portions copyright (c) 2017-2025 Technical University of Munich and       *
 * the authors' affiliations.                                                *
 *                                                                           *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may   *
 * not use this file except in compliance with the License. You may obtain a *
 * copy of the License at http://www.apache.org/licenses/LICENSE-2.0.        *
 *                                                                           *
 * ------------------------------------------------------------------------- */
/**
 * @file    sph_simulation_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef SPH_SIMULATION_BUILDER_H
#define SPH_SIMULATION_BUILDER_H

#include "base_data_type_package.h"
#include "sph_simulation_json.h"

namespace SPH
{
class SPHSystem;

/**
 * @class FluidBlockBuilder
 * @brief Builder for configuring a fluid body in a 2D or 3D simulation.
 *
 * Fluent interface example (2D):
 * @code
 *   sim.addFluidBlock("Water").block(Vec2d(LL, LH)).material(rho0_f, c_f);
 * @endcode
 * Fluent interface example (3D):
 * @code
 *   sim.addFluidBlock("Water").block(Vec3d(LL, LH, LW)).material(rho0_f, c_f);
 * @endcode
 */
class FluidBlockBuilder
{
  public:
    explicit FluidBlockBuilder(const std::string &name);

    /** Define the fluid block dimensions (starting at the coordinate origin).
     *  Use Vec2d for 2D or Vec3d for 3D builds. */
    FluidBlockBuilder &block(const Vecd &dimensions);
    /** Set the weakly-compressible fluid material parameters. */
    FluidBlockBuilder &material(Real rho0, Real c);

    const std::string &getName() const { return name_; }
    const Vecd &getDimensions() const { return dimensions_; }
    Real getRho0() const { return rho0_; }
    Real getC() const { return c_; }

  private:
    std::string name_;
    Vecd dimensions_{Vecd::Zero()};
    Real rho0_{1.0};
    Real c_{10.0};
};

/**
 * @class WallBuilder
 * @brief Builder for configuring a solid wall body in a 2D or 3D simulation.
 *
 * Fluent interface example (2D):
 * @code
 *   sim.addWall("Tank").hollowBox(Vec2d(DL, DH), BW);
 * @endcode
 * Fluent interface example (3D):
 * @code
 *   sim.addWall("Tank").hollowBox(Vec3d(DL, DH, DW), BW);
 * @endcode
 */
class WallBuilder
{
  public:
    explicit WallBuilder(const std::string &name);

    /** Define the wall as a hollow rectangular box aligned with the origin.
     *  @param domain_dimensions Inner domain dimensions (Vecd for 2D/3D).
     *  @param wall_width Thickness of the wall. */
    WallBuilder &hollowBox(const Vecd &domain_dimensions, Real wall_width);

    const std::string &getName() const { return name_; }
    const Vecd &getDomainDimensions() const { return domain_dims_; }
    Real getWallWidth() const { return BW_; }

  private:
    std::string name_;
    Vecd domain_dims_{Vecd::Zero()};
    Real BW_{0.0};
};

/**
 * @class SolverConfig
 * @brief Fluent configuration object for the SPH solver algorithm choices.
 *        Supports: useSolver().dualTimeStepping().freeSurfaceCorrection()
 */
class SolverConfig
{
  public:
    SolverConfig() = default;

    /** Enable dual time stepping (advection + acoustic sub-stepping). */
    SolverConfig &dualTimeStepping();
    /** Enable density summation with free-surface correction. */
    SolverConfig &freeSurfaceCorrection();

    bool isDualTimeStepping() const { return dual_time_stepping_; }
    bool isFreeSurfaceCorrection() const { return free_surface_correction_; }

  private:
    bool dual_time_stepping_{false};
    bool free_surface_correction_{false};
};
} // namespace SPH
#endif // SPH_SIMULATION_BUILDER_H
