#include "recording_builder.hpp"

#include "io_base_ck.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
std::string RecordingBuilder::getObserverRelationName(const ObserverConfig &observer_config)
{
    return observer_config.name_ + observer_config.observed_body_;
}
//=================================================================================================//
ObserverConfig RecordingBuilder::parseObserverConfig(const json &config)
{
    ObserverConfig observer_config;
    observer_config.name_ = config.at("name").get<std::string>();
    observer_config.observed_body_ = config.at("observed_body").get<std::string>();
    observer_config.observed_variable_ = parseVariableConfig(config.at("variable"));
    return observer_config;
}
//=================================================================================================//
void RecordingBuilder::addObserves(
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
void RecordingBuilder::addVariableToStateRecorder(
    BodyStatesRecording &state_recording, SPHBody &sph_body, const json &config)
{
    for (const auto &[type_key, _] : config.items())
    {
        if (type_key != "int_type" && type_key != "real_type" && type_key != "vector_type")
        {
            throw std::runtime_error(
                "RecordingBuilder::addVariableToStateRecorder unsupported variable type key: " +
                type_key + ". Supported keys are: int_type, real_type, vector_type.");
        }
    }

    if (config.contains("int_type"))
    {
        StdVec<std::string> int_variables = config.at("int_type").get<StdVec<std::string>>();
        for (const auto &int_var : int_variables)
        {
            state_recording.template addToWrite<int>(sph_body, int_var);
        }
    }

    if (config.contains("real_type"))
    {
        StdVec<std::string> real_variables = config.at("real_type").get<StdVec<std::string>>();
        for (const auto &real_var : real_variables)
        {
            state_recording.template addToWrite<Real>(sph_body, real_var);
        }
    }

    if (config.contains("vector_type"))
    {
        StdVec<std::string> vector_variables = config.at("vector_type").get<StdVec<std::string>>();
        for (const auto &vector_var : vector_variables)
        {
            state_recording.template addToWrite<Vecd>(sph_body, vector_var);
        }
    }
}
//=================================================================================================//
void RecordingBuilder::createBodyStatesRecording(
    SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods)
{
    auto &state_recorder = main_methods.template addBodyStateRecorder<
        BodyStatesRecordingToVtpCK>(sph_system);
    config_manager.removeEntity<MainBodyStatesRecording>("BodyStatesRecording");
    config_manager.addEntity<MainBodyStatesRecording>("BodyStatesRecording", &state_recorder);
}
//=================================================================================================//
void RecordingBuilder::finalizeBodyStatesRecording(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto &state_recorder = getBodyStatesRecording(config_manager);

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

    for (auto &body : state_recorder.getBodiesForRecording())
    {
        auto &base_particles = body->getBaseParticles();
        base_particles.dvParticlePosition()->setScalingRef(scaling_config.getScalingRef("Length"));
        auto &variables_to_write = base_particles.VariablesToWrite();
        // For now, set scaling reference for variables to write, if any, according to their unit type.
        // Variables without registered scaling references will not be scaled,
        // as they are considered as temporary variables for debug purposes only.
        constexpr int type_index_Real = DataTypeIndex<Real>::value;
        for (DiscreteVariable<Real> *variable : std::get<type_index_Real>(variables_to_write))
        {
            variable->setScalingRef(scaling_config.getScalingRef(variable->Name(), false));
        }

        constexpr int type_index_Vecd = DataTypeIndex<Vecd>::value;
        for (DiscreteVariable<Vecd> *variable : std::get<type_index_Vecd>(variables_to_write))
        {
            variable->setScalingRef(scaling_config.getScalingRef(variable->Name(), false));
        }

        constexpr int type_index_Matd = DataTypeIndex<Matd>::value;
        for (DiscreteVariable<Matd> *variable : std::get<type_index_Matd>(variables_to_write))
        {
            variable->setScalingRef(scaling_config.getScalingRef(variable->Name(), false));
        }
    }
}
//=================================================================================================//
MainBodyStatesRecording &RecordingBuilder::getBodyStatesRecording(EntityManager &config_manager)
{
    return config_manager.getEntity<MainBodyStatesRecording>("BodyStatesRecording");
}
//=================================================================================================//
void RecordingBuilder::buildObservationIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
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
void RecordingBuilder::buildEnergyRecordingIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    if (!config.contains("energy_recording"))
    {
        return;
    }

    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto &time_stepper = sim.getSPHSolver().getTimeStepper();

    auto &energy_io = main_methods.addIODynamicsGroup(sph_system);
    bool has_entries = false;

    for (const auto &entry : config.at("energy_recording"))
    {
        std::string name = entry.at("name").get<std::string>();
        std::string body_name = entry.at("body").get<std::string>();
        std::string quantity = entry.value("quantity", std::string("TotalMechanicalEnergy"));
        RealBody &body = sph_system.getBodyByName<RealBody>(body_name);

        Vecd gravity_vector = Vecd::Zero();
        if (entry.contains("gravity"))
        {
            gravity_vector = scaling_config.jsonToVecd(entry.at("gravity"), "Acceleration");
        }
        Gravity *gravity = config_manager.emplaceEntity<Gravity>(name + "_Gravity", gravity_vector);

        if (quantity == "TotalMechanicalEnergy")
        {
            // Converts the reduced quantity (computed from internally-scaled
            // Mass/Velocity/Acceleration) back to physical Joules.
            Real energy_scaling_ref = scaling_config.getScalingRef("Energy");
            auto &recorder = main_methods.template addIODynamics<
                ScaledReducedQuantityRecording, TotalMechanicalEnergyCK>(
                energy_scaling_ref, body, *gravity);
            energy_io.add(&recorder);
            has_entries = true;
        }
        else
        {
            throw std::runtime_error(
                "RecordingBuilder::buildEnergyRecordingIfPresent: unsupported quantity: " + quantity);
        }
    }

    if (!has_entries)
    {
        return;
    }

    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialObservation, [&]()
        { energy_io.writeToFile(time_stepper.getIterationStep()); });

    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::Observation, [&]()
        { energy_io.writeToFile(time_stepper.getIterationStep()); });
}
//=================================================================================================//
ParticleDynamicsGroup &RecordingBuilder::createObserverConfigurationDynamics(
    SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods)
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
IODynamicsGroup &RecordingBuilder::addObserveRecorder(
    SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods)
{
    auto &observer_io = main_methods.addIODynamicsGroup(sph_system);

    StdVec<ObserverConfig *> observer_configs = config_manager.entitiesWith<ObserverConfig>();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    if (!observer_configs.empty())
    {
        for (auto &observer_config : observer_configs)
        {
            std::string relation_name = getObserverRelationName(*observer_config);
            auto &observer_relation = sph_system.getRelationByName<
                Contact<Relation<ObserverBody, RealBody>>>(relation_name);

            observer_io.add(addObserveRecorderWithVariableConfig(
                scaling_config, observer_config->observed_variable_, main_methods, observer_relation));
        }
    }
    return observer_io;
}
//=================================================================================================//
} // namespace SPH
