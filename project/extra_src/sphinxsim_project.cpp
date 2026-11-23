#include "sphinxsim_project.h"

#include "base_body.hpp"
#include "regression_test_builder.hpp"
#include "sph_simulation.h"
#include "sphinxsys_entity.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
bool addExtraMaterial(EntityManager &config_manager, SPHBody &sph_body,
                      const json &config, const std::string &type)
{
    return false;
}
//=================================================================================================//
AbstractBidirectionalBoundary *createExtraBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config)
{
    // Second step: this branch goes to FluidDynamicsBuilder::createBiDirectionBoundary,
    // right after the "velocity" branch.
    if (config.contains("inflow_rate"))
    {
        return &createFlowRateBiDirectionBoundary(
            oriented_box_by_cell, config_manager, main_methods, config);
    }
    return nullptr;
}
//=================================================================================================//
void addExtraBiDirectionBoundaryHooks(
    EntityManager &config_manager,
    StagePipeline<InitializationHookPoint> &initialization_pipeline,
    StagePipeline<SimulationHookPoint> &simulation_pipeline,
    TimeStepper &time_stepper, const json &config)
{
    // Second step: this check replaces the first check of
    // FluidDynamicsBuilder::createBiDirectionBoundary, as
    // config.contains("pressure") + config.contains("velocity") + config.contains("inflow_rate") > 1.
    // Here createBiDirectionBoundary has already taken the pressure or velocity branch,
    // which comes before createExtraBiDirectionBoundary.
    if (config.contains("inflow_rate") && (config.contains("pressure") || config.contains("velocity")))
    {
        throw std::runtime_error(
            "Specify only one of pressure, velocity or inflow_rate for a bidirectional boundary.");
    }
    // Second step: this call goes to FluidDynamicsBuilder::addBoundaryCondition,
    // right after the call of createBiDirectionBoundary.
    addFlowRateControlIfPresent(
        config_manager, initialization_pipeline, simulation_pipeline, time_stepper, config);
}
//=================================================================================================//
void buildExtraRegressionTestIfPresent(
    SPHSystem &sph_system, EntityManager &config_manager,
    TimeStepper &time_stepper, const json &config)
{
    buildRegressionTestIfPresent(sph_system, config_manager, time_stepper, config);
}
//=================================================================================================//
BaseIO *addExtraEnergyRecording(
    SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods,
    const std::string &recording_name, Real energy_scaling_ref, RealBody &body, Gravity &gravity)
{
    // Second step: RecordingBuilder::buildEnergyRecordingIfPresent calls
    // addIODynamicsWithRegressionTest<ScaledReducedQuantityRecording, TotalMechanicalEnergyCK>
    // for every entry, in place of main_methods.addIODynamics.
    if (!config_manager.hasEntity<RegressionTestQuantity>(recording_name))
    {
        return nullptr;
    }
    return &addIODynamicsWithRegressionTest<
        RegressionScaledReducedQuantityRecording, TotalMechanicalEnergyCK>(
        sph_system, config_manager, main_methods, recording_name, energy_scaling_ref, body, gravity);
}
//=================================================================================================//
BaseIO *addExtraObserveRecorder(
    EntityManager &config_manager, MainMethods &main_methods,
    const ObserverConfig &observer_config,
    Contact<Relation<ObserverBody, RealBody>> &observer_relation)
{
    // Second step: RecordingBuilder::addObserveRecorder calls
    // addObserveRecorderWithVariableConfig of regression_test_builder.hpp for every observer.
    if (!config_manager.hasEntity<RegressionTestQuantity>(observer_config.name_))
    {
        return nullptr;
    }
    return addObserveRecorderWithVariableConfig(
        config_manager, observer_config, main_methods, observer_relation);
}
//=================================================================================================//
} // namespace SPH
