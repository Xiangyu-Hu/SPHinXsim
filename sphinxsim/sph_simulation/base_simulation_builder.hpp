#ifndef BASE_SIMULATION_BUILDER_HPP
#define BASE_SIMULATION_BUILDER_HPP

#include "base_simulation_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &SimulationBuilder::addObserverConfigurationDynamics(
    SPHSystem &sph_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    auto &observer_config_dynamics = main_methods.addParticleDynamicsGroup();

    StdVec<ObserverConfig *> observer_configs = entity_manager.entitiesWith<ObserverConfig>();
    if (!observer_configs.empty())
    {
        for (auto &observer_config : observer_configs)
        {
            std::string relation_name = getObserverRelationName(*observer_config);
            Contact<Relation<ObserverBody, RealBody>> &observer_relation = sph_system.getRelationByName<
                Contact<Relation<ObserverBody, RealBody>>>(relation_name);
            observer_config_dynamics.add(&main_methods.template addRelationDynamics(observer_relation));
        }
    }
    return observer_config_dynamics;
}
//=================================================================================================//
template <class MethodContainerType>
IODynamicsGroup &SimulationBuilder::addObserveRecorder(
    SPHSystem &sph_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    auto &observer_io = main_methods.addIODynamicsGroup(sph_system);

    StdVec<ObserverConfig *> observer_configs = entity_manager.entitiesWith<ObserverConfig>();
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
BodyStatesRecording &SimulationBuilder::addBodyStateRecorder(
    SPHSystem &sph_system, MethodContainerType &main_methods, const json &config)
{
    auto &state_recorder = main_methods.template addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);

    for (auto &body : config)
    {
        std::string body_name = body.at("name").get<std::string>();
        auto &real_body = sph_system.getBodyByName<RealBody>(body_name);
        for (auto &var : body.at("variables"))
        {
            addVariableToStateRecorder(state_recorder, real_body, var);
        }
    }
    return state_recorder;
}
//=================================================================================================//
} // namespace SPH
#endif // BASE_SIMULATION_BUILDER_HPP
