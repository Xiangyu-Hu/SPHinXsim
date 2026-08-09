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
 * @file    continuum_simulation_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef CONTINUUM_SIMULATION_BUILDER_H
#define CONTINUUM_SIMULATION_BUILDER_H

#include "base_simulation_builder.h"
#include "sph_solver.h"

namespace SPH
{
class EntityManager;
class ParticleDynamicsGroup;
template <class T>
class BaseDynamics;
class BodyStatesRecording;
class SPHBody;

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

class ContinuumSimulationBuilder : public SimulationBuilder
{
  public:
    void buildSimulation(SPHSimulation &sim, const json &config) override;
    virtual void parseSolverParameters(EntityManager &config_manager, const json &config) override;

  private:
    ContinuumSolverParameters parseContinuumSolverParameters(
        const ScalingConfig &scaling_config, const json &config);

    template <class InnerRelationType, class ContactRelationType>
    BaseDynamics<void> &addAcousticStep1stHalf(
        EntityManager &config_manager, MainMethods &main_methods,
        InnerRelationType &inner_relation, ContactRelationType &contact_relation);

    template <class InnerRelationType, class ContactRelationType>
    BaseDynamics<void> &addAcousticStep2ndHalf(
        EntityManager &config_manager, MainMethods &main_methods,
        InnerRelationType &inner_relation, ContactRelationType &contact_relation);

    template <class InnerRelationType>
    void buildShearForceIntegrationIfPresent(
        SPHSimulation &sim, MainMethods &main_methods, InnerRelationType &inner_relation);

    template <class InnerRelationType>
    ParticleDynamicsGroup &addLinearCorrectionMatrix(
        EntityManager &config_manager, MainMethods &main_methods, InnerRelationType &inner_relation);

    template <class ContactRelationType>
    void buildContactRepulsionIfPresent(
        SPHSimulation &sim, MainMethods &main_methods, ContactRelationType &contact_relation);

    template <class InnerRelationType, class ContactRelationType>
    void buildDensityRegularizationIfPresent(
        SPHSimulation &sim, MainMethods &main_methods,
        SPHBody &continuum_body, InnerRelationType &inner_relation,
        ContactRelationType &contact_relation);

    template <class InnerRelationType>
    void buildStressDiffusionIfPresent(
        SPHSimulation &sim, MainMethods &main_methods,
        SPHBody &continuum_body, InnerRelationType &inner_relation,
        BodyStatesRecording &body_state_recorder);
};
} // namespace SPH
#endif // CONTINUUM_SIMULATION_BUILDER_H
