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
 * @file    base_simulation_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef BASE_SIMULATION_BUILDER_H
#define BASE_SIMULATION_BUILDER_H

#include "simulation_scaling.h"
#include "sph_solver.h"

namespace SPH
{
// Enum for hook points for fast O(1) access
enum class SimulationHookPoint
{
    BeforeMainPhysicalTimeStep,
    BoundaryCondition,
    CouplingSynchronization,
    PositionConstraint,
    ParticleCreation,
    ParticleDeletionTagging,
    ParticleDeletion,
    Observation,
    ExtraOutput,
    ParticleSort,
    AfterUpdateConfiguration,
    UpdateConfiguration,
    AfterLinearCorrectionMatrix,
    AfterKernelGradientIntegral,
    NumHooks
};

enum class InitializationHookPoint
{
    InitialCondition,
    AfterInitialCondition,
    InitialUpdateConfiguration,
    RestartFromFile,
    UpdateConfigurationAfterRestart,
    InitialObservation,
    InitialAfterLinearCorrectionMatrix,
    InitialAfterKernelGradientIntegral,
    PreSimulationSanityCheck,
    NumHooks
};

// A staged pipeline structure
template <typename HookPointType>
struct StagePipeline
{
    std::vector<std::function<void()>> main_steps;
    std::vector<std::function<void()>> hooks[static_cast<size_t>(HookPointType::NumHooks)];

    void run_hooks(HookPointType p)
    {
        for (auto &f : hooks[static_cast<size_t>(p)])
            f();
    }

    void insert_hook(HookPointType p, std::function<void()> step)
    {
        hooks[static_cast<size_t>(p)].push_back(std::move(step));
    }
};

class SPHSimulation;
class SPHSystem;
class EntityManager;
class BaseParticles;
class MaterialBuilder;
class RecordingBuilder;
class ParticleDynamicsGroup;
class SPHBody;

template <class ReturnType>
class BaseDynamics;

struct SPHBodyConfig
{
    std::string name_;
    std::string adaptation_;
    int is_moving_ = true;
    bool has_dynamics_ = true;
    bool is_interactive_ = true;

    void setStatic();
    void setDeformable();
    void setHasDynamics();
};
using SPHBodiesConfig = StdVec<SPHBodyConfig *>;

struct VariableConfig
{
    std::string type_;
    std::string name_;
};
VariableConfig parseVariableConfig(const json &config);

struct SolverCommonConfig
{
    Real end_time_{0.0};
    Real output_interval_{0.1};
    UnsignedInt screen_interval_{100};
    UnsignedInt observation_interval_{200};
};

struct RestartConfig
{
    int save_interval_{1000};
    int restore_step_{0};
    bool summary_enabled_{false};
};

struct GravityConfig
{
    Real gravity_ = 0.0;
    StdVec<std::string> enabled_solid_bodies_{};
};
class SimulationBuilder
{
  public:
    SimulationBuilder();
    virtual ~SimulationBuilder();
    virtual void buildSimulation(SPHSimulation &sim, const json &config) = 0;
    virtual void parseSolverParameters(EntityManager &config_manager, const json &config);
    static void parseScheduledEvents(SPHSimulation &sim, const json &config, bool &on_flag);
    static RestartConfig parseRestartConfig(const json &config);

  protected:
    void buildFluidBodies(SPHSystem &sph_system, EntityManager &config_manager, const json &config);
    void buildContinuumBodies(SPHSystem &sph_system, EntityManager &config_manager, const json &config);
    void buildSolidBodies(SPHSystem &sph_system, EntityManager &config_manager, const json &config);
    void buildUpdateConfiguration(SPHSimulation &sim, MainMethods &main_methods, const json &config);

    void buildExternalForceIfPresent(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    void buildInitialConditionIfPresent(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    void buildRestartFromFileIfPresent(SPHSimulation &sim, MainMethods &main_methods, const json &config);

  private:
    std::unique_ptr<MaterialBuilder> material_builder_ptr_;
    SolverCommonConfig parseSolverCommonConfig(const ScalingConfig &scaling_config, const json &config);

    void buildCellLinkedListDynamics(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    void buildFluidRelationDynamics(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    void buildContinuumRelationDynamics(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    void buildSolidRelationDynamics(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    void addUpdateConfigurationDynamicsToPipeline(
        SPHSimulation &sim, EntityManager &config_manager, ParticleDynamicsGroup &configuration_dynamics);

    template <class IdentifierType>
    BaseDynamics<void> &addVariableAssignment(
        MainMethods &method_container, IdentifierType &identifier,
        const ScalingConfig &scaling_config, const json &config);
};
} // namespace SPH
#endif // BASE_SIMULATION_BUILDER_H
