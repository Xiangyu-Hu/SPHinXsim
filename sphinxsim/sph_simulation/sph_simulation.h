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

#include <optional>

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
 *   sim.initializeSimulation();
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
    SPHSimulation(const fs::path &config_path);
    ~SPHSimulation() {};

    /** Override output/restart/reload root folder (mainly for tests). */
    void resetOutputRoot(const fs::path &output_root);

    /** Build all SPH objects and run the simulation until end_time. */
    void run(Real end_time);

    /** Build the simulation from a JSON object (see class docstring). */
    void buildSimulationFromJson(const json &config);

    /** Initialize all executable dynamics after a successful build. */
    void initializeSimulation();

    /** Load JSON config from the path given at construction, then build simulation. */
    void loadConfig();

  private:
    void defineSPHSystem(const json &config);
    FluidBody &addFluidBody(const json &config);
    SolidBody &addWall(const json &config, const Vecd &domain_dims, Real boundary_width);
    void enableGravity(const json &config);
    void addObserver(const json &config);
    SolverConfig &useSolver(const json &config);
    void resetConfigurationState();
    void buildExecutableState();

    std::filesystem::path config_path_;
    EntityManager entity_manager_;
    Real end_time_{0.0};
    Vecd gravity_{Vecd::Zero()};
    bool gravity_enabled_{false};

    std::vector<std::string> fluid_body_names_;
    std::vector<std::string> wall_body_names_;
    std::vector<std::string> observer_body_names_;

    std::unique_ptr<SolverConfig> solver_config_;
    std::unique_ptr<SPHSolver> sph_solver_;

    FluidBody *water_block_ptr_{nullptr};
    SolidBody *wall_boundary_ptr_{nullptr};
    std::unique_ptr<Inner<>> water_block_inner_;
    std::unique_ptr<Contact<>> water_wall_contact_;
    std::vector<std::unique_ptr<Contact<>>> observer_contacts_;

    BaseDynamics<void> *water_cell_linked_list_{nullptr};
    BaseDynamics<void> *wall_cell_linked_list_{nullptr};
    BaseDynamics<void> *water_block_update_complex_relation_{nullptr};
    std::vector<BaseDynamics<void> *> observer_relation_dynamics_;
    BaseDynamics<void> *particle_sort_{nullptr};

    BaseDynamics<void> *wall_boundary_normal_direction_{nullptr};
    BaseDynamics<void> *water_advection_step_setup_{nullptr};
    BaseDynamics<void> *water_update_particle_position_{nullptr};
    BaseDynamics<void> *constant_gravity_{nullptr};
    BaseDynamics<void> *fluid_linear_correction_matrix_{nullptr};
    BaseDynamics<void> *fluid_acoustic_step_1st_half_{nullptr};
    BaseDynamics<void> *fluid_acoustic_step_2nd_half_{nullptr};
    BaseDynamics<void> *fluid_density_regularization_{nullptr};

    BaseDynamics<Real> *fluid_advection_time_step_{nullptr};
    BaseDynamics<Real> *fluid_acoustic_time_step_{nullptr};

    BodyStatesRecording *body_state_recorder_{nullptr};
    std::vector<BaseIO *> observer_pressure_outputs_;

    size_t advection_steps_{1};
    bool executable_state_ready_{false};

    SPHSystem &getSPHSystem() { return *sph_system_ptr_.get(); };
    std::unique_ptr<SPHSystem> sph_system_ptr_;
};
} // namespace SPH
#endif // SPH_SIMULATION_H
