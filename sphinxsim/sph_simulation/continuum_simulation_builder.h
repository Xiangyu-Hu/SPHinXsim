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

#include "sph_simulation_utility.h"

namespace SPH
{
class EntityManager;
class ParticleDynamicsGroup;
template <class T>
class BaseDynamics;
class SPHBody;

struct ContinuumSolverParameters
{
    Real acoustic_cfl_{0.4};
    Real advection_cfl_{0.2};
    Real linear_correction_matrix_coeff_{0.5};
    Real contact_numerical_damping_{0.5};
    Real shear_stress_damping_{0.0};
    Real hourglass_factor_{2.0};
    int screen_interval_{100};
};

class ContinuumSimulationBuilder : public SimulationBuilder
{
  public:
    void buildSimulation(SPHSimulation &sim, const json &config) override;

  private:
    UnsignedInt advection_steps_{0};
    ContinuumSolverParameters solver_parameters_;
    StdVec<BaseDynamics<std::pair<Real, UnsignedInt>> *> output_evolving_variables_bounds_[3];
    StdVec<std::string> evolving_variables_names_[3];

    void updateSolverParameters(SPHSimulation &sim, const json &config);
    void outputEvolvingVariablesBounds();

    template <class MethodContainerType, class InnerRelationType>
    BaseDynamics<void> &addAcousticStep1stHalf(
        EntityManager &entity_manager, MethodContainerType &method_container, InnerRelationType &inner_relation);

    template <class MethodContainerType, class InnerRelationType>
    BaseDynamics<void> &addAcousticStep2ndHalf(
        EntityManager &entity_manager, MethodContainerType &method_container, InnerRelationType &inner_relation);

    template <class MethodContainerType, class InnerRelationType>
    ParticleDynamicsGroup &addShearForceIntegration(
        EntityManager &entity_manager, MethodContainerType &method_container, InnerRelationType &inner_relation);

    template <class MethodContainerType>
    void addOutputEvolvingVariablesBounds(MethodContainerType &method_container, SPHBody &sph_body);
};
} // namespace SPH
#endif // CONTINUUM_SIMULATION_BUILDER_H
