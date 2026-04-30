#ifndef BASE_SIMULATION_BUILDER_HPP
#define BASE_SIMULATION_BUILDER_HPP

#include "base_simulation_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
BodyStatesRecording &SimulationBuilder::createBodyStatesRecording(
    SPHSystem &sph_system, EntityManager &config_manager,
    MethodContainerType &main_methods, const json &config)
{
    auto &state_recorder = main_methods.template addBodyStateRecorder<
        BodyStatesRecordingToVtpCK>(sph_system);
    if (config.contains("extra_state_recording"))
    {
        for (auto &body : config.at("extra_state_recording"))
        {
            std::string body_name = body.at("name").get<std::string>();
            auto &real_body = sph_system.getBodyByName<RealBody>(body_name);
            for (auto &var : body.at("variables"))
            {
                addVariableToStateRecorder(state_recorder, real_body, var);
            }
        }
    }
    return state_recorder;
}
//=================================================================================================//
template <class MethodContainerType>
void SimulationBuilder::buildObservationIfPresent(
    SPHSimulation &sim, MethodContainerType &main_methods, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();

    if (config.contains("observers"))
    {
        addObserves(sph_system, config_manager, config.at("observers"));
        auto &observer_config_dynamics =
            createObserverConfigurationDynamics(sph_system, config_manager, main_methods);
        auto &observer_io = addObserveRecorder(sph_system, config_manager, main_methods);

        auto &time_stepper = sim.getSPHSolver().getTimeStepper();

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialObservation, [&]()
            {
                observer_config_dynamics.exec();
                observer_io.writeToFile(time_stepper.getIterationStep()); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::Observation, [&]()
            {
                    observer_config_dynamics.exec();
                    observer_io.writeToFile(time_stepper.getIterationStep()); });
    }
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &SimulationBuilder::createObserverConfigurationDynamics(
    SPHSystem &sph_system, EntityManager &config_manager, MethodContainerType &main_methods)
{
    auto &observer_config_dynamics = main_methods.addParticleDynamicsGroup();

    StdVec<ObserverConfig *> observer_configs = config_manager.entitiesWith<ObserverConfig>();
    if (!observer_configs.empty())
    {
        for (auto &observer_config : observer_configs)
        {
            ObserverBody &observer_body = sph_system.getBodyByName<ObserverBody>(observer_config->name_);
            RealBody &observed_body = sph_system.getBodyByName<RealBody>(observer_config->observed_body_);
            auto &observer_relation = sph_system.addContactRelation(observer_body, observed_body);
            observer_config_dynamics.add(&main_methods.addRelationDynamics(observer_relation));
        }
    }
    return observer_config_dynamics;
}
//=================================================================================================//
template <class MethodContainerType>
IODynamicsGroup &SimulationBuilder::addObserveRecorder(
    SPHSystem &sph_system, EntityManager &config_manager, MethodContainerType &main_methods)
{
    auto &observer_io = main_methods.addIODynamicsGroup(sph_system);

    StdVec<ObserverConfig *> observer_configs = config_manager.entitiesWith<ObserverConfig>();
    if (!observer_configs.empty())
    {
        for (auto &observer_config : observer_configs)
        {
            std::string relation_name = getObserverRelationName(*observer_config);
            auto &observer_relation = sph_system.getRelationByName<
                Contact<Relation<ObserverBody, RealBody>>>(relation_name);

            observer_io.add(addObserveRecorderWithVariableConfig(
                observer_config->observed_variable_, main_methods, observer_relation));
        }
    }
    return observer_io;
}
//=================================================================================================//
template <class MethodContainerType, class ObserverRelationType>
BaseIO *SimulationBuilder::addObserveRecorderWithVariableConfig(
    const VariableConfig &variable_config, MethodContainerType &main_methods,
    ObserverRelationType &observer_relation)
{
    if (variable_config.type_ == "Real")
    {
        return &main_methods.template addObserveRecorder<Real>(
            variable_config.name_, observer_relation);
    }

    if (variable_config.type_ == "Vecd")
    {
        return &main_methods.template addObserveRecorder<Vecd>(
            variable_config.name_, observer_relation);
    }

    throw std::runtime_error(
        "SimulationBuilder::addObserveRecorderWithVariableConfig: no supported variable type found!");
}
//=================================================================================================//
template <class MethodContainerType>
void SimulationBuilder::buildExternalForceIfPresent(
    SPHSimulation &sim, MethodContainerType &main_methods, SPHBody &sph_body, const json &config)
{
    auto &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntityByName<ScalingConfig>("ScalingConfig");
    auto &initialization_pipeline = sim.getInitializationPipeline();

    if (config.contains("gravity"))
    {
        auto &constant_gravity =
            main_methods.template addStateDynamics<GravityForceCK<Gravity>>(
                sph_body, Gravity(scaling_config.jsonToVecd(config.at("gravity"), "Acceleration")));

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { constant_gravity.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH
#endif // BASE_SIMULATION_BUILDER_HPP
