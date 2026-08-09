#include "base_simulation_builder.hpp"

#include "material_builder.h"
#include "recording_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
VariableConfig parseVariableConfig(const json &config)
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

    throw std::runtime_error("parseVariableConfig not supported variable type.");
}
//=================================================================================================//
SimulationBuilder::SimulationBuilder() : material_builder_ptr_(std::make_unique<MaterialBuilder>()) {}
//=================================================================================================//
SimulationBuilder ::~SimulationBuilder() = default;
//=================================================================================================//
void SimulationBuilder::buildFluidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    for (const auto &fb : config)
    {
        const std::string name = fb.at("name").get<std::string>();
        Shape &fluid_shape = config_manager.getEntity<Shape>(name);
        auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, fluid_body, fb.at("material"));
        if (fb.contains("particle_reserve_factor"))
        {
            ParticleBuffer<ReserveSizeFactor> inlet_buffer(
                scaling_config.jsonToReal(fb.at("particle_reserve_factor"), "Dimensionless"));
            fluid_body.generateParticlesWithReserve<BaseParticles, Reload>(inlet_buffer, name);
        }
        else
        {
            fluid_body.generateParticles<BaseParticles, Reload>(name);
        }
    }
}
//=================================================================================================//
void SimulationBuilder::buildContinuumBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &cb : config)
    {
        const std::string name = cb.at("name").get<std::string>();
        Shape &shape = config_manager.getEntity<Shape>(name);
        auto &continuum_body = sph_system.addBody<RealBody>(shape, name);
        material_builder_ptr_->addMaterial(config_manager, continuum_body, cb.at("material"));
        continuum_body.generateParticles<BaseParticles, Reload>(name);
    }
}
//=================================================================================================//
void SimulationBuilder::buildSolidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &sb : config)
    {
        const std::string name = sb.at("name").get<std::string>();
        Shape &solid_shape = config_manager.getEntity<Shape>(name);
        auto &solid_body = sph_system.addBody<SolidBody>(solid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, solid_body, sb.at("material"));
        BaseParticles &reload_particles = solid_body.generateParticles<BaseParticles, Reload>(name);
        reload_particles.reloadExtraVariable<Vecd>("NormalDirection");
    }
}
//=================================================================================================//
void SimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    config_manager.emplaceEntity<
        SolverCommonConfig>("SolverCommonConfig", parseSolverCommonConfig(scaling_config, config));

    if (config.contains("restart"))
    {
        config_manager.emplaceEntity<RestartConfig>(
            "RestartConfig", parseRestartConfig(config.at("restart")));
    }
}
//=================================================================================================//
RestartConfig SimulationBuilder::parseRestartConfig(const json &config)
{
    RestartConfig restart_config;
    if (config.contains("save_interval"))
        restart_config.save_interval_ = config.at("save_interval").get<int>();
    restart_config.restore_step_ = config.at("restore_step").get<int>();
    if (config.contains("summary_enabled"))
        restart_config.summary_enabled_ = config.at("summary_enabled").get<bool>();
    return restart_config;
}
//=================================================================================================//
SolverCommonConfig SimulationBuilder::parseSolverCommonConfig(
    const ScalingConfig &scaling_config, const json &config)
{
    SolverCommonConfig solver_common_config;

    if (config.contains("end_time"))
        solver_common_config.end_time_ = scaling_config.jsonToReal(config.at("end_time"), "Time");

    if (config.contains("output_interval"))
        solver_common_config.output_interval_ = scaling_config.jsonToReal(config.at("output_interval"), "Time");
    else
        solver_common_config.output_interval_ = solver_common_config.end_time_ / 100.0; // default to 100 output frames

    if (config.contains("screen_interval"))
        solver_common_config.screen_interval_ = config.at("screen_interval").get<UnsignedInt>();

    if (config.contains("observation_interval"))
        solver_common_config.observation_interval_ = config.at("observation_interval").get<UnsignedInt>();
    return solver_common_config;
}
//=================================================================================================//
void SimulationBuilder::parseScheduledEvents(SPHSimulation &sim, const json &config, bool &on_flag)
{
    auto &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto &time_stepper = sim.getSPHSolver().getTimeStepper();

    on_flag = false; // disable until switch_on_time
    Real switch_on_time = scaling_config.jsonToReal(config.at("switch_on_time"), "Time");
    time_stepper.getEventScheduler().schedule(
        switch_on_time, [&]()
        { on_flag = true; });

    if (config.contains("duration"))
    {
        Real duration = scaling_config.jsonToReal(config.at("duration"), "Time");
        Real switch_off_time = switch_on_time + duration;
        time_stepper.getEventScheduler().schedule(
            switch_off_time, [&]()
            { on_flag = false; });
    }
}
//=================================================================================================//
void SimulationBuilder::buildExternalForceIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, SPHBody &real_body, const json &config)
{
    auto &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto &initialization_pipeline = sim.getInitializationPipeline();

    if (config.contains("gravity"))
    {
        auto &constant_gravity =
            main_methods.template addStateDynamics<GravityForceCK<Gravity>>(
                real_body, Gravity(scaling_config.jsonToVecd(config.at("gravity"), "Acceleration")));

        initialization_pipeline.insert_hook(
            InitializationHookPoint::AfterInitialCondition, [&]()
            { constant_gravity.exec(); });
    }
}
//=================================================================================================//
void SimulationBuilder::buildInitialConditionIfPresent(
    SPHSimulation &sim, MainMethods &method_container, const json &config)
{
    if (config.contains("initial_conditions"))
    {
        auto &sph_system = sim.getSPHSystem();
        auto &config_manager = sim.getConfigManager();
        auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
        auto &initialization_pipeline = sim.getInitializationPipeline();

        auto &dynamics = method_container.addParticleDynamicsGroup();
        for (const auto &ic : config.at("initial_conditions"))
        {
            const std::string name = ic.at("body_name").get<std::string>();
            auto &real_body = sph_system.getBodyByName<RealBody>(name);
            for (const auto &assignment : ic.at("assignments"))
            {
                if (assignment.contains("region"))
                {
                    std::string region_name = assignment.at("region").get<std::string>();
                    auto &oriented_box = config_manager.getEntity<OrientedBox>(region_name);
                    auto &body_region = real_body.template addBodyPart<OrientedBoxByParticle>(oriented_box);
                    dynamics.add(&addVariableAssignment(method_container, body_region, scaling_config, assignment));
                }
                else
                {
                    dynamics.add(&addVariableAssignment(method_container, real_body, scaling_config, assignment));
                }
            }
        }

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { dynamics.exec(); });
    }
}
//=================================================================================================//
void SimulationBuilder::buildRestartFromFileIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    EntityManager &config_manager = sim.getConfigManager();
    SPHSystem &sph_system = sim.getSPHSystem();
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();

    if (config_manager.hasEntity<RestartConfig>("RestartConfig"))
    {
        auto &restart_config = config_manager.getEntity<RestartConfig>("RestartConfig");
        sph_system.setRestartStep(restart_config.restore_step_);
        auto &restart_io = main_methods.template addIODynamics<RestartIOCK>(
            sph_system, restart_config.summary_enabled_);

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ExtraOutput, [&]()
            { 
                if (time_stepper.getIterationStep() % restart_config.save_interval_ == 0)
                {
                    restart_io.writeToFile(time_stepper.getIterationStep());
                } });

        auto &initialization_pipeline = sim.getInitializationPipeline();
        if (restart_config.restore_step_ != 0)
        {
            initialization_pipeline.insert_hook(
                InitializationHookPoint::RestartFromFile, [&]()
                { 
                    time_stepper.setRestartStep(restart_config.restore_step_);
                    restart_io.readRestartFiles(restart_config.restore_step_); });

            BaseDynamics<void> &update_configuration =
                config_manager.getEntity<BaseDynamics<void>>("UpdateConfiguration");
            initialization_pipeline.insert_hook(
                InitializationHookPoint::UpdateConfigurationAfterRestart, [&]()
                { update_configuration.exec(); });
        }
    }
}
//=================================================================================================//
} // namespace SPH
