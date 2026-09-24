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
#include <map>
#include <string>
#include <utility>
#include <vector>
namespace fs = std::filesystem;

namespace SPH
{
class GeometryBuilder;
class RecordingBuilder;
class SimulationBuilder;
class ParticleGeneration;

class SPHSimulation
{
  public:
    SPHSimulation(const fs::path &config_path);
    ~SPHSimulation();
    void resetOutputRoot(const fs::path &output_root, bool keep_existing = false);
    void buildGeometries();
    std::map<std::string, std::pair<std::vector<double>, std::vector<double>>> getShapeBounds();
    void generateParticles();
    void buildSimulation();
    void initializeSimulation();
    void run();
    void stepTo(Real target_time);
    void stepBy(Real interval);

  protected:
    friend class SimulationBuilder;
    friend class ParticleGeneration;
    friend class FluidSimulationBuilder;
    friend class ContinuumSimulationBuilder;
    friend class ConstraintBuilder;
    friend class FluidDynamicsBuilder;
    friend class ContinuumDynamicsBuilder;
    friend class ThermalDynamicsBuilder;
    friend class SolidDynamicsBuilder;
    friend class RecordingBuilder;

    SPHSystem &defineSPHSystem(const json &config);
    SPHSolver &defineSPHSolver(SimulationBuilder &simulation_builder, const json &config);
    SPHSystem &getSPHSystem() { return *sph_system_ptr_; };
    SPHSolver &getSPHSolver() { return *sph_solver_ptr_; };
    EntityManager &getConfigManager();
    StagePipeline<InitializationHookPoint> &getInitializationPipeline();
    StagePipeline<SimulationHookPoint> &getSimulationPipeline();
    // Contact relations owned here for interactions that need the plain
    // Contact<> type rather than the typed relation the system creates.
    template <typename... Args>
    Contact<> &addStructureContact(Args &&...args)
    {
        return *structure_contacts_keeper_.createPtr<Contact<>>(std::forward<Args>(args)...);
    }

  private:
    std::filesystem::path config_path_;
    EntityManager config_manager_;
    StagePipeline<InitializationHookPoint> initialization_pipeline_;
    StagePipeline<SimulationHookPoint> simulation_pipeline_;
    std::unique_ptr<SPHSystem> sph_system_ptr_;
    std::unique_ptr<SPHSolver> sph_solver_ptr_;
    UniquePtrsKeeper<Contact<>> structure_contacts_keeper_;
    bool geometry_built_{false};
    bool particles_generated_{false};
    bool executable_simulation_state_ready_{false};
    json loadConfig();
};
} // namespace SPH
#endif // SPH_SIMULATION_H
