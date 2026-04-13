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

#include "data_type.h"

#include <nlohmann/json.hpp>
using json = nlohmann::json;

namespace SPH
{
/** Convert a JSON array [x, y] or [x, y, z] to Vecd (extra elements are
 * ignored). */
Vecd jsonToVecd(const nlohmann::json &arr);

#ifdef SPHINXSYS_2D
Transform jsonToTransform(const nlohmann::json &config);
#else
Transform jsonToTransform(const nlohmann::json &config);
#endif

// Enum for hook points for fast O(1) access
enum class SimulationHookPoint
{
    ForcePrior,
    BoundaryConditions,
    PositionConstraints,
    ParticleCreation,
    ParticleDeletion,
    ExtraOutputs,
    ParticleSort,
    NumHooks
};

enum class InitializationHookPoint
{
    HostSteps,
    InitialConditions,
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

struct RestartConfig
{
    bool enabled_{false};
    int save_interval_{1000};
    int restore_step_{0};
    bool summary_enabled_{false};
};

struct SolverCommonConfig
{
    Real end_time_{0.0};
    Real output_interval_{0.1};
    UnsignedInt screen_interval_{100};
};

class SimulationBuilder
{
  public:
    SimulationBuilder();
    virtual ~SimulationBuilder();
    virtual void buildSimulation(SPHSimulation &sim, const json &config) = 0;
    virtual void parseSolverParameters(EntityManager &entity_manager, const json &config);

  protected:
    void addFluidBodies(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addContinuumBodies(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addSolidBodies(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addObservers(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);

  private:
    std::unique_ptr<MaterialBuilder> material_builder_ptr_;
    SolverCommonConfig parseSolverCommonConfig(const json &config);
    void parseParticleReload(const json &config, BaseParticles &reload_particles);
    RestartConfig parseRestartConfig(const json &config);
};
} // namespace SPH
#endif // BASE_SIMULATION_BUILDER_H
