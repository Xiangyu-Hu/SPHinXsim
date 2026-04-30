#include "sph_simulation.h"

#include "continuum_simulation_builder.h"
#include "fluid_simulation_builder.h"
#include "geometry_builder.h"
#include "material_builder.h"
#include "particle_generation.h"

namespace SPH
{
//=================================================================================================//
SPHSimulation::SPHSimulation(const fs::path &config_path)
    : config_path_(config_path),
      geometry_builder_ptr_(std::make_unique<GeometryBuilder>())
{
    IO::initEnvironment();
}
//=================================================================================================//
SPHSimulation::~SPHSimulation() = default;
//=================================================================================================//
void SPHSimulation::resetOutputRoot(const fs::path &output_root, bool keep_existing)
{
    IOEnvironment &io_env = IO::getEnvironment();
    if (!fs::exists(output_root))
    {
        fs::create_directories(output_root);
    }
    io_env.resetOutputFolder((output_root / "output").string(), keep_existing);
    io_env.resetRestartFolder((output_root / "restart").string(), keep_existing);
    io_env.resetReloadFolder((output_root / "reload").string(), keep_existing);
}
//=================================================================================================//
SPHSystem &SPHSimulation::defineSPHSystem()
{
    SystemDomainConfig &system_config = config_manager_.getEntity<
        SystemDomainConfig>("SystemDomainConfig");
    sph_system_ptr_ = std::make_unique<SPHSystem>(
        system_config.system_bounds_, system_config.particle_spacing_);
    return *sph_system_ptr_.get();
}
//=================================================================================================//
SPHSolver &SPHSimulation::defineSPHSolver(SimulationBuilder &simulation_builder, const json &config)
{
    simulation_builder.parseSolverParameters(config_manager_, config.at("solver_parameters"));
    sph_solver_ptr_ = std::make_unique<SPHSolver>(getSPHSystem());
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
EntityManager &SPHSimulation::getConfigManager()
{
    return config_manager_;
}
//=================================================================================================//
void SPHSimulation::createParticlesGeneration(const json &config)
{
    if (config.at("build_and_run").get<bool>())
    {
        particle_generation_ptr_ = std::make_unique<ParticleGeneration>();
        particle_generation_ptr_->buildParticleGeneration(*this, config.at("settings"));
        particle_generation_ptr_->runRelaxation();
    }
}
//=================================================================================================//
void SPHSimulation::buildSimulationFromJson(const json &config)
{
    config_manager_.emplaceEntity<ScalingConfig>("ScalingConfig", config);
    geometry_builder_ptr_->createGeometries(config_manager_, config.at("geometries"));
    createParticlesGeneration(config.at("particle_generation"));

    if (config.contains("simulation_type"))
    {
        std::string simulation_type = config.at("simulation_type").get<std::string>();

        if (simulation_type == "fluid_dynamics")
        {
            FluidSimulationBuilder fluid_simulation_builder;
            fluid_simulation_builder.buildSimulation(*this, config);
            return;
        }

        if (simulation_type == "continuum_dynamics")
        {
            ContinuumSimulationBuilder continuum_simulation_builder;
            continuum_simulation_builder.buildSimulation(*this, config);
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
    SolverCommonConfig &solver_common_config =
        config_manager_.getEntity<SolverCommonConfig>("SolverCommonConfig");

    stepTo(solver_common_config.end_time_);
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
void SPHSimulation::runParticleGeneration()
{
    if (!particle_generation_ptr_)
    {
        std::cerr << "SPHSimulation::ParticleGeneration: ParticleGeneration not found.\n";
        exit(1);
    }
    particle_generation_ptr_->runRelaxation();
}
//=================================================================================================//
} // namespace SPH
