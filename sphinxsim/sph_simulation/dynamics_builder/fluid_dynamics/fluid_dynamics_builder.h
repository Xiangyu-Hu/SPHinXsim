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
 * @file    fluid_dynamics_builder.h
 * @brief   Shared builders for fluid-like auxiliary dynamics.
 * @author  Xiangyu Hu
 */

#ifndef FLUID_DYNAMICS_BUILDER_H
#define FLUID_DYNAMICS_BUILDER_H

#include "base_simulation_builder.h"
#include "sph_solver.h"

namespace SPH
{
class TimeStepper;
class OrientedBoxByParticle;
class OrientedBoxByCell;
class RealBody;
class FluidBody;
namespace fluid_dynamics
{
class AbstractBidirectionalBoundary;
}

struct FluidSolverConfig
{
    Real acoustic_cfl_{0.6};
    Real advection_cfl_{0.25};
    Real max_velocity_factor_{1.0};
    std::string surface_type_ = "free_surface";
    std::string kernel_correction_{"linear"};
    bool particle_deletion_{false};
    bool particle_sorting_{false};
    UnsignedInt sort_frequency_{0};
    bool emitter_on_{false};
};

class FluidDynamicsBuilder
{
  public:
    static BaseDynamics<void> &addAdvectionStepSetup(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addUpdateParticlePosition(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addAcousticStep1stHalf(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addAcousticStep2ndHalf(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addLinearCorrectionMatrix(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addDensityRegularization(SPHSimulation &sim, MainMethods &main_methods);
    static void buildViscousForceIfPresent(SPHSimulation &sim, MainMethods &main_methods);
    static void buildSurfaceIndicationIfOpenBoundary(SPHSimulation &sim, MainMethods &main_methods);
    static void buildTransportVelocityFormulationIfNotFreeSurface(SPHSimulation &sim, MainMethods &main_methods);

    template <class FluidType, class InnerRelationType, class ContactRelationType>
    static BaseDynamics<void> &buildDensityRegularization(
        SPHSimulation &sim, MainMethods &main_methods, InnerRelationType &inner_relation,
        ContactRelationType &contact_relation, const std::string &surface_type);

    static void buildBoundaryConditionsIfPresent(
        SPHSimulation &sim, MainMethods &main_methods, const json &config);
    static BaseDynamics<Real> &addAdvectionTimeStep(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<Real> &addAcousticTimeStep(SPHSimulation &sim, MainMethods &main_methods);

  private:
    static BaseDynamics<void> &addTransportVelocityCorrection(
        MainMethods &main_methods, SPHBody &sph_body, FluidSolverConfig &fluid_solver_config);

    static void addBoundaryCondition(
        SPHSimulation &sim, MainMethods &main_methods, const json &config);

    static fluid_dynamics::AbstractBidirectionalBoundary &createBiDirectionBoundary(
        OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
        MainMethods &main_methods, const json &config);

    template <template <typename...> class AcousticHalfStepForOneBody, class InnerRelationType>
    static BaseDynamics<void> &addAcousticHalfStepForOneBody(
        SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods);

    template <class RiemannSolverType, class KernelCorrectionType, class AcousticHalfStepType>
    static void addAcousticHalfStepWithSolidBodies(
        SPHSimulation &sim, AcousticHalfStepType &interaction, std::string body_name);

    static BaseDynamics<Real> &addAcousticTimeStepForOneBody(
        SPHSimulation &sim, FluidBody &fluid_body, MainMethods &main_methods);
};
} // namespace SPH
#endif // FLUID_DYNAMICS_BUILDER_H
