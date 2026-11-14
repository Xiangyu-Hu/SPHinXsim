/**
 * @file    sphinxsim_project.h
 * @brief   Project: flow-rate-prescribed bidirectional boundary
 *          ("type": "bi_directional" with "inflow_rate" and "inflow_area").
 * @details Extra classes of this project and where they go in the second step:
 *          - flow_rate_boundary_state.h               -> sphinxsys fluid_boundary_state.h
 *          - flow_rate_bidirectional_boundary_ck.h    -> sphinxsys bidirectional_boundary_ck.h
 *          - flow_rate_bidirectional_boundary_ck.hpp  -> sphinxsys bidirectional_boundary_ck.hpp
 *          - createExtraBiDirectionBoundary() and addExtraBiDirectionBoundaryHooks()
 *            -> FluidDynamicsBuilder in fluid_boundary_builder.cpp
 *          Interface patches in sphinxsim/sph_simulation/, all in fluid_boundary_builder.cpp:
 *          the include of this header and one call to each of the two functions above.
 * @author  Xiangyu Hu
 */

#ifndef SPHINXSIM_PROJECT_H
#define SPHINXSIM_PROJECT_H

#include "base_simulation_builder.h"
#include "flow_rate_bidirectional_boundary_ck.h"
#include "flow_rate_boundary_state.h"
#include "simulation_scaling.h"
namespace SPH
{
class EntityManager;

bool addExtraMaterial(
    EntityManager &config_manager, SPHBody &sph_body,
    const json &config, const std::string &type);

template <class InnerRelationType>
bool addExtraSolidRelaxation1stHalf(
    EntityManager &config_manager, ParticleDynamicsGroup &solid_relaxation_1st_half,
    MainMethods &main_methods, InnerRelationType &inner_relation);

fluid_dynamics::AbstractBidirectionalBoundary *createExtraBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config);

void addExtraBiDirectionBoundaryHooks(
    EntityManager &config_manager,
    StagePipeline<InitializationHookPoint> &initialization_pipeline,
    StagePipeline<SimulationHookPoint> &simulation_pipeline,
    TimeStepper &time_stepper, const json &config);
} // namespace SPH

#endif // SPHINXSIM_PROJECT_H
