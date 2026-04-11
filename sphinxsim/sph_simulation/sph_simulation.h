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
 * @file    sph_simulation.h
 * @brief   High-level user-facing API for setting up and running SPH
 * simulations. Provides a fluent builder interface to configure domain, bodies,
 * solver, and run the simulation with minimal boilerplate. Works for both 2D
 * and 3D simulations via the dimension-agnostic Vecd type.
 * @author  Xiangyu Hu
 */

#ifndef SPH_SIMULATION_H
#define SPH_SIMULATION_H

#include "base_simulation_builder.h"
#include "sphinxsys.h"

#include <filesystem>
namespace fs = std::filesystem;

namespace SPH
{
class GeometryBuilder;
class SimulationBuilder;

struct SystemDomainConfig
{
    BoundingBoxd system_domain_bounds_;
    Real particle_spacing_;
};

struct RestartConfig
{
    bool enabled{false};
    int save_interval{1000};
    int restore_step{0};
    bool summary_enabled{false};
};

class SPHSimulation
{
  public:
    SPHSimulation(const fs::path &config_path);
    ~SPHSimulation() {};
    void resetOutputRoot(const fs::path &output_root, bool keep_existing = false);
    void loadConfig();
    void runParticleRelaxation();
    void initializeSimulation();
    void run();
    void stepTo(Real target_time);
    void stepBy(Real interval);

  private:
    std::filesystem::path config_path_;
    EntityManager entity_manager_;
    GeometryBuilder &geometry_builder_;
    StagePipeline<InitializationHookPoint> initialization_pipeline_;
    StagePipeline<SimulationHookPoint> simulation_pipeline_;
    std::unique_ptr<SPHSolver> sph_solver_ptr_;
    UniquePtrKeeper<SimulationBuilder> simulation_builder_ptr_;
    Real end_time_{0.0};
    Real output_interval_{0.1};
    bool executable_simulation_state_ready_{false};
    RestartConfig restart_config_;

    void buildSimulationFromJson(const json &config);
    void parseSystemDomainConfig(const json &config);
    void parseRestartConfig(const json &config);
    void parseParticleReload(const json &config, BaseParticles &reload_particles);
    void handleParticleRelaxation(const json &config);

  protected:
    friend class SimulationBuilder;
    friend class ParticleRelaxationBuilder;
    friend class FluidSimulationBuilder;
    friend class ContinuumSimulationBuilder;
    friend class ConstraintBuilder;

    SPHSystem &defineSPHSystem();
    SPHSolver &defineSPHSolver(SPHSystem &sph_system, const json &config);
    StagePipeline<InitializationHookPoint> &getInitializationPipeline();
    StagePipeline<SimulationHookPoint> &getSimulationPipeline();
    Real getOutputInterval() { return output_interval_; };
    RestartConfig &getRestartConfig() { return restart_config_; };
    EntityManager &getEntityManager();
    SPHSolver &getSPHSolver() { return *sph_solver_ptr_; };
    void addFluidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addContinuumBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addSolidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addObserver(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
};
} // namespace SPH
#endif // SPH_SIMULATION_H
