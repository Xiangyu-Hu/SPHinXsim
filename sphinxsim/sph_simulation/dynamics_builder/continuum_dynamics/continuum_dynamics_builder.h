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
 * @file    continuum_dynamics_builder.h
 * @brief   tbd.
 * @author  Xiangyu Hu
 */

#ifndef CONTINUUM_DYNAMICS_BUILDER_H
#define CONTINUUM_DYNAMICS_BUILDER_H

#include "base_simulation_builder.h"
#include "sph_solver.h"

namespace SPH
{
class TimeStepper;
class OrientedBoxByParticle;
class OrientedBoxByCell;
class RealBody;

struct ContinuumSolverParameters
{
    Real acoustic_cfl_{0.4};
    Real advection_cfl_{0.2};
    Real linear_correction_matrix_coeff_{0.5};
    Real contact_numerical_damping_{0.5};
    Real shear_stress_damping_{0.0};
    Real hourglass_factor_{2.0};
    Real plastic_riemann_dissipation_factor_{20.0 * (Real)Dimensions};
    std::string surface_type_ = "free_surface";
};

class ContinuumDynamicsBuilder
{
  public:
    static BaseDynamics<void> &addAdvectionStepSetup(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addUpdateParticlePosition(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addAcousticStep1stHalf(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addAcousticStep2ndHalf(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<void> &addLinearCorrectionMatrix(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<Real> &addAdvectionTimeStep(SPHSimulation &sim, MainMethods &main_methods);
    static BaseDynamics<Real> &addAcousticTimeStep(SPHSimulation &sim, MainMethods &main_methods);
    static void buildShearForceIntegrationIfPresent(SPHSimulation &sim, MainMethods &main_methods);
    static void buildContactRepulsionIfPresent(SPHSimulation &sim, MainMethods &main_methods);
    static void buildDensityRegularizationIfPresent(SPHSimulation &sim, MainMethods &main_methods);
    static void buildStressDiffusionIfPresent(SPHSimulation &sim, MainMethods &main_methods);

  private:
    template <class InnerRelationType>
    static BaseDynamics<void> &addAcousticStep1stHalfForOneBody(
        SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods);

    template <class InnerRelationType>
    static BaseDynamics<void> &addAcousticStep2ndHalfForOneBody(
        SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods);
};
} // namespace SPH
#endif // CONTINUUM_DYNAMICS_BUILDER_H
