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
 * @file    particle_relaxation_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef PARTICLE_RELAXATION_BUILDER_H
#define PARTICLE_RELAXATION_BUILDER_H

#include "base_simulation_builder.h"

namespace SPH
{
class RelaxationSystem;
class EntityManager;
class SPHSolver;
class ParticleDynamicsGroup;
template <class T>
class BaseDynamics;

struct CommonBodyConfig
{
    std::string name_;
    bool is_relaxation_body_ = false;
    bool is_solid_body_ = false;
};

struct RelaxationBodyConfig
{
    std::string name_;
    bool with_level_set_ = false;
    std::vector<std::string> contact_bodies_;
};

struct AllBodiesConfig
{
    std::vector<CommonBodyConfig> all_bodies_;
    std::vector<RelaxationBodyConfig> relaxation_bodies_; // particles in these bodies will be relaxed
};
struct RelaxationParameters
{
    UnsignedInt total_iterations{1000};
};

enum class RelaxationHookPoint
{
    Initialization,
    Constraints,
    NumHooks
};

enum class ReloadIOHookPoint
{
    NumHooks
};

class ParticleRelaxation
{
  public:
    ~ParticleRelaxation();
    void buildParticleRelaxation(SPHSimulation &sim, const json &config);
    void runRelaxation();

  private:
    AllBodiesConfig bodies_config_;
    RelaxationParameters relaxation_parameters_;
    std::unique_ptr<RelaxationSystem> relaxation_system_ptr_;
    std::unique_ptr<SPHSolver> sph_solver_ptr_;
    StagePipeline<RelaxationHookPoint> relaxation_pipeline_;
    StagePipeline<ReloadIOHookPoint> reload_io_pipeline_;

    RelaxationSystem &defineRelaxationSystem(EntityManager &entity_manager, const json &config);
    SPHSolver &defineSPHSolver(RelaxationSystem &relaxation_system, const json &config);
    RelaxationParameters parseRelaxationParameters(const json &config);
    void addAllBodies(RelaxationSystem &relaxation_system, EntityManager &entity_manager, const json &config);
    RelaxationBodyConfig parseRelaxationBodyConfig(std::string body_name, const json &config);
    void defineBodyRelations(RelaxationSystem &relaxation_system);

    template <class MethodContainerType>
    ParticleDynamicsGroup &randomizeParticlePositions(RelaxationSystem &relaxation_system, MethodContainerType &main_methods);

    template <class MethodContainerType>
    ParticleDynamicsGroup &addDummyBodiesCellLinkedListDynamics(
        RelaxationSystem &relaxation_system, MethodContainerType &main_methods);

    template <class MethodContainerType>
    ParticleDynamicsGroup &addConfigurationDynamics(RelaxationSystem &relaxation_system, MethodContainerType &main_methods);

    template <class MethodContainerType>
    ParticleDynamicsGroup &addRelaxationResidue(
        RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods);

    template <class MethodContainerType>
    BaseDynamics<Real> &addRelaxationScaling(
        RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods);

    template <class MethodContainerType>
    ParticleDynamicsGroup &addRelaxationPositionUpdate(
        RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods);

    template <class MethodContainerType>
    ParticleDynamicsGroup &addBodyNormalDirection(
        RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods);

    template <class MethodContainerType>
    ParticleDynamicsGroup &addRelaxationConstraints(
        RelaxationSystem &relaxation_system, EntityManager &entity_manager,
        MethodContainerType &main_methods, const json &config);
};
} // namespace SPH
#endif // PARTICLE_RELAXATION_BUILDER_H
