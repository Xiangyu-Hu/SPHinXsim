/**
 * @file    flow_rate_boundary_builder.h
 * @brief   Builder functions of the flow-rate-prescribed bidirectional boundary
 *          ("type": "bi_directional" with "inflow_rate" and "inflow_area").
 * @details Project stage only. In the second step the two functions below become static
 *          members of FluidDynamicsBuilder, declared in
 *          sphinxsim/sph_simulation/dynamics_builder/fluid_dynamics/fluid_dynamics_builder.h
 *          right after createVelocityBiDirectionBoundary. They are called here from
 *          createExtraBiDirectionBoundary() and addExtraBiDirectionBoundaryHooks()
 *          in sphinxsim_project.cpp.
 */

#ifndef FLOW_RATE_BOUNDARY_BUILDER_H
#define FLOW_RATE_BOUNDARY_BUILDER_H

#include "base_simulation_builder.h"

namespace SPH
{
class EntityManager;
class OrientedBoxByCell;
class TimeStepper;

namespace fluid_dynamics
{
class AbstractBidirectionalBoundary;
}

fluid_dynamics::AbstractBidirectionalBoundary &createFlowRateBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config);

void addFlowRateControlIfPresent(
    EntityManager &config_manager,
    StagePipeline<InitializationHookPoint> &initialization_pipeline,
    StagePipeline<SimulationHookPoint> &simulation_pipeline,
    TimeStepper &time_stepper, const json &config);
} // namespace SPH

#endif // FLOW_RATE_BOUNDARY_BUILDER_H
