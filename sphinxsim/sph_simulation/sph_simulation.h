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

#include "sph_simulation_utility.h"
#include "sphinxsys.h"

#include <filesystem>
namespace fs = std::filesystem;

namespace SPH
{
class GeometryBuilder;
class SimulationBuilder;

struct SPHSystemConfig
{
    BoundingBoxd system_domain_bounds_;
    Real particle_spacing_;
};

/**
 * @class SPHSimulation
 * @brief High-level facade for a 2D or 3D SPH simulation using the CK execution
 * backend.
 *
 * Typical usage:
 * @code
 *   SPHSimulation sim("config.json");
 *   sim.loadConfig();
 *   sim.initializeSimulation();
 *   sim.run(20.0);
 * @endcode
 *
 * The JSON config schema is:
 * @code
 * {
 *   "domain"      : { "lower_bound": [0.0, 0.0], "upper_bound": [DL, DH] },
 *   "particle_spacing": 0.02,
 *   "particle_boundary_buffer": 4,
 *   "fluid_bodies" : [{
 *     "name": "Water",
 *     "geometry": { "type": "bounding_box",
 *                   "lower_bound": [0.0, 0.0], "upper_bound": [LL, LH] },
 *     "material": { "type": "weakly_compressible_fluid",
 *                   "density": 1000.0, "sound_speed": 20.0 }
 *   }],
 *   "solid_bodies" : [{
 *     "name": "Tank",
 *     "geometry": { "type": "container_box",
 *                   "inner_lower_bound": [0.0, 0.0], "inner_upper_bound": [DL, DH],
 *                   "thickness": 0.08 },
 *     "material": { "type": "rigid_body" }
 *   }],
 *   "gravity"     : [0.0, -9.81],
 *   "observers"   : [{ "name": "Probe", "positions": [[0.5, 0.2]] }],
 *   "solver"      : { "dual_time_stepping": true, "free_surface_correction": true },
 *   "end_time"    : 20.0
 * }
 * @endcode
 */
class SPHSimulation
{
  public:
    SPHSimulation(const fs::path &config_path);
    ~SPHSimulation() {};

    /** Override output/restart/reload root folder (mainly for tests). */
    void resetOutputRoot(const fs::path &output_root, bool keep_existing = false);

    /** Build all SPH objects and run the simulation until end_time. */
    void run(Real end_time);

    void runParticleRelaxation();

    /** Initialize all executable dynamics after a successful build. */
    void initializeSimulation();

    /** Load JSON config from the path given at construction, then build simulation. */
    void loadConfig();

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
    bool executable_particle_relaxation_ready_{false};
    bool executable_simulation_state_ready_{false};

    void buildSimulationFromJson(const json &config);
    SPHSystemConfig &getSPHSystemConfig(const json &config);
    void parseParticleReload(const json &config, BaseParticles &reload_particles);

  protected:
    friend class SimulationBuilder;
    friend class ParticleRelaxationBuilder;
    friend class FluidSimulationBuilder;
    friend class ContinuumSimulationBuilder;

    SPHSystem &defineSPHSystem(const json &config);
    RelaxationSystem &defineRelaxationSystem(const json &config);
    SPHSolver &defineSPHSolver(SPHSystem &sph_system, const json &config);
    StagePipeline<InitializationHookPoint> &getInitializationPipeline();
    StagePipeline<SimulationHookPoint> &getSimulationPipeline();
    Real getOutputInterval() { return output_interval_; };
    EntityManager &getEntityManager();
    void addRelaxationBody(RelaxationSystem &relaxation_system, EntityManager &entity_manager, const json &config);
    void addFluidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addContinuumBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addSolidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addObserver(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
};
} // namespace SPH
#endif // SPH_SIMULATION_H
