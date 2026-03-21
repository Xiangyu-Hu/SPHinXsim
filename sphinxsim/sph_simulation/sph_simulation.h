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

#include "base_data_type_package.h"
#include "sph_simulation_builder.h"
#include "sph_simulation_json.h"
#include "sphinxsys.h"

namespace SPH
{
/**
 * @class SPHSimulation
 * @brief High-level facade for a 2D or 3D SPH simulation using the CK execution
 * backend.
 *
 * Typical usage:
 * @code
 *   SPHSimulation sim("config.json");
 *   sim.loadConfig();
 *   sim.run(20.0);
 * @endcode
 *
 * The JSON config schema is:
 * @code
 * {
 *   "domain"      : { "dimensions": [DL, DH] },
 *   "particle_spacing": 0.02,
 *   "particle_boundary_buffer": 4,
 *   "fluid_blocks": [{ "name": "Water", "dimensions": [LL, LH],
 *                      "density": 1000.0, "sound_speed": 20.0 }],
 *   "walls"       : [{ "name": "Tank" }],
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
    SPHSimulation(const std::filesystem::path &config_path);
    ~SPHSimulation() {};

    /** Build all SPH objects and run the simulation until end_time. */
    void run(Real end_time);

    /** Configure the simulation from a JSON object (see class docstring). */
    void loadFromJson(const json &config);

    /** Load JSON config from the path given at construction, then call loadFromJson. */
    void loadConfig();

  private:
    void defineSPHSystem(const json &config, Real particle_spacing, const Vecd &domain_dims, Real boundary_width);
    FluidBody &addFluidBody(const json &config);
    SolidBody &addWall(const json &config, const Vecd &domain_dims, Real boundary_width);
    void enableGravity(const json &config);
    void addObserver(const json &config);
    SolverConfig &useSolver(const json &config);

    std::filesystem::path config_path_;
    EntityManager entity_manager_;
    Real end_time_{0.0};
    Vecd gravity_{Vecd::Zero()};
    bool gravity_enabled_{false};

    std::vector<std::string> fluid_body_names_;
    std::vector<std::string> wall_body_names_;

    struct ObserverEntry
    {
        std::string name;
        StdVec<Vecd> positions;
    };
    std::vector<ObserverEntry> observers_;

    std::unique_ptr<SolverConfig> solver_config_;
    SPHSystem &getSPHSystem() { return *sph_system_ptr_.get(); };
    std::unique_ptr<SPHSystem> sph_system_ptr_;
};
} // namespace SPH
#endif // SPH_SIMULATION_H
