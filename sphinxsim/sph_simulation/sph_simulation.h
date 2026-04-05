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
class BaseSimulationBuilder;

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
    void resetOutputRoot(const fs::path &output_root);

    /** Build all SPH objects and run the simulation until end_time. */
    void run(Real end_time);

    /** Initialize all executable dynamics after a successful build. */
    void initializeSimulation();

    /** Load JSON config from the path given at construction, then build simulation. */
    void loadConfig();

  private:
    std::filesystem::path config_path_;
    std::unique_ptr<SPHSystem> sph_system_ptr_;
    EntityManager entity_manager_;
    StagePipeline<InitializationHookPoint> initialization_pipeline_;
    StagePipeline<SimulationHookPoint> simulation_pipeline_;
    std::unique_ptr<SPHSolver> sph_solver_ptr_;
    UniquePtrKeeper<BaseSimulationBuilder> simulation_builder_ptr_;
    Real end_time_{0.0};
    bool executable_state_ready_{false};

    void buildSimulationFromJson(const json &config);

  protected:
    friend class BaseSimulationBuilder;
    friend class FluidSimulationBuilder;

    SPHSystem &defineSPHSystem(const json &config);
    SPHSolver &defineSPHSolver(SPHSystem &sph_system, const json &config);
    StagePipeline<InitializationHookPoint> &getInitializationPipeline();
    StagePipeline<SimulationHookPoint> &getSimulationPipeline();
    EntityManager &getEntityManager();
    void addShape(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addMaterial(EntityManager &entity_manager, SPHBody &sph_body, const json &config);
    template <class MethodContainerType>
    void addFluidBoundaryConditions(MethodContainerType &method_container, EntityManager &entity_manager, const json &config);
    GeometricOps parseGeometricOp(const std::string &op_str);
#ifdef SPHINXSYS_2D
    MultiPolygon parseMultiPolygon(const json &config);
#endif
    void addFluidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addSolidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
    void addObserver(SPHSystem &sph_system, EntityManager &entity_manager, const json &config);
};
} // namespace SPH
#endif // SPH_SIMULATION_H
