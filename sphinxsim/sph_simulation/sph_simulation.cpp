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
    : config_path_(config_path)
{
    IOEnvironment &io_env = IO::initEnvironment();
    io_env.resetInputFolder((config_path_.parent_path()).string(), true);
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
SPHSystem &SPHSimulation::defineSPHSystem(const json &config)
{
    SystemDomainConfig &system_config = config_manager_.getEntity<
        SystemDomainConfig>("SystemDomainConfig");
    sph_system_ptr_ = std::make_unique<SPHSystem>(
        system_config.system_bounds_, system_config.particle_spacing_);
    auto &scaling_config = config_manager_.getEntity<ScalingConfig>("ScalingConfig");
    sph_system_ptr_->svPhysicalTime().setScalingRef(scaling_config.getScalingRef("Time"));
    sph_system_ptr_->writeSystemDomainShapeToVtp(scaling_config.getScalingRef("Length"));
    sph_system_ptr_->setLogLevel(SimulationBuilder::parseLoglevel(config));
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
void SPHSimulation::generateParticles()
{
    if (!geometry_built_)
    {
        std::cerr << "SPHSimulation::generateParticles: Geometries are not built. "
                     "Call buildGeometries() before generateParticles().\n";
        exit(1);
    }

    json config = loadConfig();
    auto &restart_config = *config_manager_.emplaceEntity<RestartConfig>("RestartConfig");
    if (config.contains("restart"))
    {
        restart_config = SimulationBuilder::parseRestartConfig(config.at("restart"));
    }

    json particle_generation_config = config.at("particle_generation");
    if (particle_generation_config.at("build_and_run").get<bool>() &&
        restart_config.restore_step_ == 0)
    {
        ParticleGeneration particle_generation;
        particle_generation.buildParticleGeneration(*this, particle_generation_config.at("settings"));
        particle_generation.runRelaxation();
    }
    particles_generated_ = true;
}
//=================================================================================================//
void SPHSimulation::buildGeometries()
{
    json config = loadConfig();
    config_manager_.clear();
    config_manager_.emplaceEntity<ScalingConfig>("ScalingConfig", config);
    GeometryBuilder::createGeometries(config_manager_, config.at("geometries"));
    geometry_built_ = true;
    executable_simulation_state_ready_ = false;
}
//=================================================================================================//
std::map<std::string, std::pair<std::vector<double>, std::vector<double>>> SPHSimulation::getShapeBounds()
{
    return GeometryBuilder::getShapeBoundsFromConfigManager(config_manager_);
}
//=================================================================================================//
void SPHSimulation::buildSimulation()
{
    if (!particles_generated_)
    {
        std::cerr << "SPHSimulation::buildSimulation: particles not generated. "
                     "Call generateParticles() before buildSimulation().\n";
        exit(1);
    }

    json config = loadConfig();
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
json SPHSimulation::loadConfig()
{
    json config;
    std::ifstream file(config_path_);
    if (!file.is_open())
    {
        throw std::runtime_error(
            "SPHSimulation::loadConfig: unable to open config file " + config_path_.string());
    }
    file >> config;
    return config;
}
//=================================================================================================//
void SPHSimulation::initializeSimulation()
{
    if (!sph_solver_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::initializeSimulation: simulation is not built. "
            "Call buildSimulation() first.");
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
} // namespace SPH
