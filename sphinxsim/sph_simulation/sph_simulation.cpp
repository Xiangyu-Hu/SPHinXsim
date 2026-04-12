#include "sph_simulation.h"

#include "continuum_simulation_builder.h"
#include "fluid_simulation_builder.h"
#include "geometry_builder.h"
#include "material_builder.h"
#include "particle_relaxation_builder.h"

namespace SPH
{
//=================================================================================================//
SPHSimulation::SPHSimulation(const fs::path &config_path)
    : config_path_(config_path),
      geometry_builder_(*entity_manager_.emplaceEntity<GeometryBuilder>("GeometryBuilder")) {}
//=================================================================================================//
void SPHSimulation::resetOutputRoot(const fs::path &output_root, bool keep_existing)
{
    IOEnvironment &io_env = IO::initEnvironment();
    if (!fs::exists(output_root))
    {
        fs::create_directories(output_root);
    }
    io_env.resetOutputFolder((output_root / "output").string(), keep_existing);
    io_env.resetRestartFolder((output_root / "restart").string(), keep_existing);
    io_env.resetReloadFolder((output_root / "reload").string(), keep_existing);
}
//=================================================================================================//
void SPHSimulation::parseSystemDomainConfig(const json &config)
{
    SystemDomainConfig system_config;
    system_config.system_domain_bounds_ = geometry_builder_.parseBoundingBox(config.at("domain"));
    system_config.particle_spacing_ = config.at("particle_spacing").get<Real>();
    entity_manager_.emplaceEntity<SystemDomainConfig>("SystemDomainConfig", system_config);
}
//=================================================================================================//
SPHSystem &SPHSimulation::defineSPHSystem()
{
    SystemDomainConfig &system_config = entity_manager_.getEntityByName<SystemDomainConfig>("SystemDomainConfig");
    return *entity_manager_.emplaceEntity<SPHSystem>(
        "SPHSystem", system_config.system_domain_bounds_, system_config.particle_spacing_);
}
//=================================================================================================//
SPHSolver &SPHSimulation::defineSPHSolver(SPHSystem &sph_system, const json &config)
{

    sph_solver_ptr_ = std::make_unique<SPHSolver>(sph_system);
    return *sph_solver_ptr_.get();
}
//=================================================================================================//
StagePipeline<InitializationHookPoint> &SPHSimulation::getInitializationPipeline()
{
    return initialization_pipeline_;
}
//=================================================================================================//
StagePipeline<SimulationHookPoint> &SPHSimulation::getSimulationPipeline()
{
    return simulation_pipeline_;
}
//=================================================================================================//
EntityManager &SPHSimulation::getEntityManager()
{
    return entity_manager_;
}
//=================================================================================================//
void SPHSimulation::handleParticleRelaxation(const json &config)
{
    if (config.at("build_and_run").get<bool>())
    {
        entity_manager_.emplaceEntity<ParticleRelaxationBuilder>("ParticleRelaxation")
            ->buildParticleRelaxation(*this, config.at("settings"));
        runParticleRelaxation();
    }
}
//=================================================================================================//
void SPHSimulation::buildSimulationFromJson(const json &config)
{
    parseSystemDomainConfig(config);
    geometry_builder_.addGeometries(entity_manager_, config);

    if (config.contains("particle_relaxation"))
    {
        handleParticleRelaxation(config.at("particle_relaxation"));
    }

    if (config.contains("simulation_type"))
    {
        entity_manager_.emplaceEntity<MaterialBuilder>("MaterialBuilder");

        std::string simulation_type = config.at("simulation_type").get<std::string>();

        if (simulation_type == "fluid_dynamics")
        {
            simulation_builder_ptr_.createPtr<FluidSimulationBuilder>()->buildSimulation(*this, config);
            return;
        }

        if (simulation_type == "continuum_dynamics")
        {
            simulation_builder_ptr_.createPtr<ContinuumSimulationBuilder>()->buildSimulation(*this, config);
            return;
        }

        throw std::runtime_error(
            "SPHSimulation::buildSimulationFromJson: unsupported simulation type: " + simulation_type);
    }
}
//=================================================================================================//
void SPHSimulation::loadConfig()
{
    std::ifstream file(config_path_);
    if (!file.is_open())
    {
        throw std::runtime_error(
            "SPHSimulation::loadConfig: unable to open config file " + config_path_.string());
    }
    json config;
    file >> config;

    if (config.contains("end_time"))
        end_time_ = config.at("end_time").get<Real>();

    if (config.contains("output_interval"))
        output_interval_ = config.at("output_interval").get<Real>();
    else
        output_interval_ = end_time_ / 100.0; // default to 100 output frames

    if (config.contains("restart"))
    {
        parseRestartConfig(config.at("restart"));
    }

    buildSimulationFromJson(config);
}
//=================================================================================================//
void SPHSimulation::initializeSimulation()
{
    if (!sph_solver_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::initializeSimulation: simulation is not built. "
            "Call loadConfig() or buildSimulationFromJson() first.");
    }

    for (auto &step : initialization_pipeline_.main_steps)
    {
        step(); // each step touches all cells internally
    }

    executable_simulation_state_ready_ = true;
}
//=================================================================================================//
void SPHSimulation::run()
{
    stepTo(end_time_);
}
//=================================================================================================//
void SPHSimulation::stepTo(Real target_time)
{
    if (!executable_simulation_state_ready_)
    {
        std::cerr << "SPHSimulation::run: Simulation is not initialized. "
                     "Call initializeSimulation() before run.\n";
        return;
    }

    TimeStepper &time_stepper = sph_solver_ptr_->getTimeStepper();
    while (!time_stepper.isEndTime(target_time))
    {
        for (auto &step : simulation_pipeline_.main_steps)
        {
            step(); // each step touches all cells internally
        }
    }
}
//=================================================================================================//
void SPHSimulation::stepBy(Real interval)
{
    TimeStepper &time_stepper = sph_solver_ptr_->getTimeStepper();
    Real present_time_ = time_stepper.getPhysicalTime();
    stepTo(present_time_ + interval);
}
//=================================================================================================//
void SPHSimulation::runParticleRelaxation()
{
    if (!entity_manager_.hasEntity<ParticleRelaxationBuilder>("ParticleRelaxation"))
    {
        std::cerr << "SPHSimulation::runParticleRelaxation: Particle relaxation builder not found.\n";
        exit(1);
    }

    ParticleRelaxationBuilder &relaxation_builder =
        entity_manager_.getEntityByName<ParticleRelaxationBuilder>("ParticleRelaxation");
    relaxation_builder.runRelaxation();
}
//=================================================================================================//
void SPHSimulation::parseRestartConfig(const json &config)
{
    restart_config_.enabled = config.at("enabled").get<bool>();
    if (config.contains("save_interval"))
        restart_config_.save_interval = config.at("save_interval").get<int>();
    restart_config_.restore_step = config.at("restore_step").get<int>();
    if (config.contains("summary_enabled"))
        restart_config_.summary_enabled = config.at("summary_enabled").get<bool>();
}
//=================================================================================================//
} // namespace SPH
