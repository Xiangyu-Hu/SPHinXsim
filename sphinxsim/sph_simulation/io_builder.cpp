#include "io_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
RestartConfig IOBuilder::parseRestartConfig(const json &config)
{
    RestartConfig restart_config;
    restart_config.enabled_ = config.at("enabled").get<bool>();
    if (config.contains("save_interval"))
        restart_config.save_interval_ = config.at("save_interval").get<int>();
    restart_config.restore_step_ = config.at("restore_step").get<int>();
    if (config.contains("summary_enabled"))
        restart_config.summary_enabled_ = config.at("summary_enabled").get<bool>();
    return restart_config;
}
//=================================================================================================//
std::string IOBuilder::getObserverRelationName(const ObserverConfig &observer_config)
{
    return observer_config.name_ + observer_config.observed_body_;
}
//=================================================================================================//
ObserverConfig IOBuilder::parseObserverConfig(const json &config)
{
    ObserverConfig observer_config;
    observer_config.name_ = config.at("name").get<std::string>();
    observer_config.observed_body_ = config.at("observed_body").get<std::string>();
    observer_config.observed_variable_ = parseVariableConfig(config.at("variable"));
    return observer_config;
}
//=================================================================================================//
void IOBuilder::addObserves(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    for (const auto &ob : config)
    {
        ObserverConfig observer_config = parseObserverConfig(ob);
        std::string name = observer_config.name_;
        config_manager.emplaceEntity<ObserverConfig>(name, observer_config);

        StdVec<Vecd> positions;
        if (ob.contains("positions"))
        {
            for (const auto &p : ob.at("positions"))
            {
                positions.push_back(scaling_config.jsonToVecd(p, "Length"));
            }
        }

        ObserverBody &observer_body = sph_system.addBody<ObserverBody>(name);
        observer_body.generateParticles<ObserverParticles>(positions);
    }
}
//=================================================================================================//
VariableConfig IOBuilder::parseVariableConfig(const json &config)
{
    VariableConfig variable_config;
    if (config.contains("real_type"))
    {
        variable_config.type_ = "Real";
        variable_config.name_ = config.at("real_type").get<std::string>();
        return variable_config;
    }

    if (config.contains("vector_type"))
    {
        variable_config.type_ = "Vecd";
        variable_config.name_ = config.at("vector_type").get<std::string>();
        return variable_config;
    }

    throw std::runtime_error("IOBuilder::parseVariableConfig not supported variable type.");
}
//=================================================================================================//
void IOBuilder::addVariableToStateRecorder(
    BodyStatesRecording &state_recording, SPHBody &sph_body, const json &config)
{
    if (config.contains("real_type"))
    {
        StdVec<std::string> real_variables = config.at("real_type").get<StdVec<std::string>>();
        for (const auto &real_var : real_variables)
        {
            state_recording.template addToWrite<Real>(sph_body, real_var);
        }
        return;
    }

    if (config.contains("vector_type"))
    {
        StdVec<std::string> vector_variables = config.at("vector_type").get<StdVec<std::string>>();
        for (const auto &vector_var : vector_variables)
        {
            state_recording.template addToWrite<Vecd>(sph_body, vector_var);
        }
        return;
    }

    throw std::runtime_error("IOBuilder::addVariableToStateRecorder not supported variable type.");
}
//=================================================================================================//
} // namespace SPH
