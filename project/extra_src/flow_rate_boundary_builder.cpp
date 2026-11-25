/**
 * @file    flow_rate_boundary_builder.cpp
 * @brief   Implementation of the functions in flow_rate_boundary_builder.h.
 * @details Project stage only. In the second step the two functions below are to be moved to
 *          sphinxsim/sph_simulation/dynamics_builder/fluid_dynamics/fluid_boundary_builder.cpp,
 *          right after FluidDynamicsBuilder::createBiDirectionBoundary, as
 *          FluidDynamicsBuilder::createFlowRateBiDirectionBoundary and
 *          FluidDynamicsBuilder::addFlowRateControlIfPresent, together with the
 *          FlowRateRecording alias and the include of "recording_builder.hpp".
 *          createBiDirectionBoundary then calls the first one for "inflow_rate", and
 *          addBoundaryCondition calls the second one right after createBiDirectionBoundary.
 *          The error messages already carry the names of the second step.
 */

#include "flow_rate_boundary_builder.h"

#include "flow_rate_bidirectional_boundary_ck.hpp"
#include "flow_rate_boundary_state.h"
#include "material_builder.h"
#include "regression_test_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
// Internal (nondimensional) values on purpose: RegressionTestTimeAverage ignores mean values
// below 0.005, so a physical Q of 2e-8 m^3/s would pass without being checked.
using FlowRateRecording = ReducedQuantityRecording<MainExecutionPolicy, BufferFlowRateCK>;
//=================================================================================================//
AbstractBidirectionalBoundary &createFlowRateBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    SPHBody &sph_body = oriented_box_by_cell.getSPHBody();
    std::string body_name = sph_body.Name();
    std::string oriented_box_name = config.at("oriented_box").get<std::string>();
    Real q_target = scaling_config.jsonToReal(config.at("inflow_rate"), "VolumetricFlowRate");
    if (!config.contains("inflow_area"))
    {
        throw std::runtime_error(
            "FluidDynamicsBuilder::createFlowRateBiDirectionBoundary: inflow_rate at OrientedBox '" +
            oriented_box_name + "' requires inflow_area.");
    }
    // cross-section area is given by the user, no assumption on the shape of the inlet
    Real length_ref = scaling_config.getScalingRef("Length");
    Real area = config.at("inflow_area").get<Real>() / (length_ref * length_ref);
    if (area <= Real(0))
    {
        throw std::runtime_error(
            "FluidDynamicsBuilder::createFlowRateBiDirectionBoundary: inflow_area at OrientedBox '" +
            oriented_box_name + "' must be positive.");
    }
    // the oriented box name keys the controller and its output files, so it must be unique
    if (config_manager.hasEntity<VelocityIncrementCK<MainExecutionPolicy>>(
            oriented_box_name + "VelocityIncrement"))
    {
        throw std::runtime_error(
            "FluidDynamicsBuilder::createFlowRateBiDirectionBoundary: OrientedBox '" +
            oriented_box_name + "' already has an inflow_rate boundary condition.");
    }
    // restart: the controller averages the buffer pressure before the first acoustic step
    sph_body.getBaseParticles().addEvolvingVariable<Real>("Pressure");
    auto &velocity_increment_manager = main_methods.addGeneralDynamics<VelocityIncrementCK>(
        oriented_box_by_cell, q_target, area);
    // handed over to addFlowRateControlIfPresent, which owns the pipeline access
    config_manager.addEntity(oriented_box_name + "VelocityIncrement", &velocity_increment_manager);

    if (config.contains("flow_rate_recording"))
    {
        // named by the user, as an observer or an energy recording, and also the file name
        std::string recording_name = config.at("flow_rate_recording").get<std::string>();
        // A regression test fixes its database file names at construction, i.e. before
        // setFullPath below, after the body part and "BufferFlowRate".
        auto &flow_rate_recording = addIODynamicsWithRegressionTest<
            ReducedQuantityRecording, BufferFlowRateCK>(
            sph_body.getSPHSystem(), config_manager, main_methods, recording_name, oriented_box_by_cell, area);
        flow_rate_recording.setFullPath(recording_name);
        config_manager.addEntity(oriented_box_name + "FlowRateRecording", &flow_rate_recording);
    }

    if (config_manager.hasEntity<WeaklyCompressibleFluid>(body_name + "WeaklyCompressibleFluid"))
    {
        return main_methods.addGeneralDynamics<
            BidirectionalBoundaryCK, LinearCorrectionCK, FlowRatePrescribed<WeaklyCompressibleFluid>>(
            oriented_box_by_cell, velocity_increment_manager.svReferencePressure());
    }

    if (sph_body.isMatterMaterial<WeaklyCompressibleMixture>())
    {
        return main_methods.addGeneralDynamics<
            BidirectionalBoundaryCK, LinearCorrectionCK, FlowRatePrescribed<WeaklyCompressibleMixture>>(
            oriented_box_by_cell, velocity_increment_manager.svReferencePressure());
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::createFlowRateBiDirectionBoundary: inflow_rate at OrientedBox '" +
        oriented_box_name + "' requires a weakly compressible fluid or mixture.");
}
//=================================================================================================//
void addFlowRateControlIfPresent(
    EntityManager &config_manager,
    StagePipeline<InitializationHookPoint> &initialization_pipeline,
    StagePipeline<SimulationHookPoint> &simulation_pipeline,
    TimeStepper &time_stepper, const json &config)
{
    using FlowController = VelocityIncrementCK<MainExecutionPolicy>;
    std::string oriented_box_name = config.at("oriented_box").get<std::string>();
    if (!config_manager.hasEntity<FlowController>(oriented_box_name + "VelocityIncrement"))
    {
        return;
    }

    auto *flow_controller =
        &config_manager.getEntity<FlowController>(oriented_box_name + "VelocityIncrement");
    // The recorder reduces BufferFlowRateCK, the same class the controller measures with,
    // right before the controller in the same hook, so both see the same particles and
    // velocities, before this step's increment.
    FlowRateRecording *flow_rate_recording = nullptr;
    if (config_manager.hasEntity<FlowRateRecording>(oriented_box_name + "FlowRateRecording"))
    {
        flow_rate_recording =
            &config_manager.getEntity<FlowRateRecording>(oriented_box_name + "FlowRateRecording");
    }
    auto *time_stepper_ptr = &time_stepper;
    // written at observation steps, i.e. the same iteration interval as the other recordings
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialAfterLinearCorrectionMatrix,
        [flow_controller, flow_rate_recording, time_stepper_ptr]()
        {
            if (flow_rate_recording != nullptr)
            {
                flow_rate_recording->writeToFile(time_stepper_ptr->getIterationStep());
            }
            flow_controller->updateVelocityIncrement();
        });
    simulation_pipeline.insert_hook(
        SimulationHookPoint::AfterLinearCorrectionMatrix,
        [flow_controller, flow_rate_recording, time_stepper_ptr]()
        {
            if (flow_rate_recording != nullptr && time_stepper_ptr->isObservationStep())
            {
                flow_rate_recording->writeToFile(time_stepper_ptr->getIterationStep());
            }
            flow_controller->updateVelocityIncrement();
        });
}
//=================================================================================================//
} // namespace SPH
