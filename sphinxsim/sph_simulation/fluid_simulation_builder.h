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
 * @file    fluid_simulation_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef FLUID_SIMULATION_BUILDER_H
#define FLUID_SIMULATION_BUILDER_H

#include "base_simulation_builder.h"

namespace SPH
{

class TimeStepper;
class AlignedBoxByParticle;
class AlignedBoxByCell;
namespace fluid_dynamics
{
class AbstractBidirectionalBoundary;
}

struct FluidSolverConfig
{
    Real acoustic_cfl_{0.6};
    Real advection_cfl_{0.25};
    bool free_surface_correction_{true};
    bool particle_deletion_{false};
};

class FluidSimulationBuilder : public SimulationBuilder
{
  public:
    void buildSimulation(SPHSimulation &sim, const json &config) override;
    virtual void parseSolverParameters(EntityManager &entity_manager, const json &config) override;

  private:
    FluidSolverConfig parseFluidSolverConfig(const json &config);

    template <class MethodContainerType>
    void addBoundaryConditions(
        SPHSimulation &sim, MethodContainerType &method_container, const json &config);

    template <class MethodContainerType>
    fluid_dynamics::AbstractBidirectionalBoundary &addBiDirectionBoundary(
        AlignedBoxByCell &aligned_box_by_cell, EntityManager &entity_manager,
        MethodContainerType &main_methods, const json &config);
};
} // namespace SPH
#endif // FLUID_SIMULATION_BUILDER_H
