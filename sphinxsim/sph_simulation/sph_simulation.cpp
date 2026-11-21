#include "sph_simulation.h"

#include "continuum_simulation_builder.h"
#include "fluid_simulation_builder.h"
#include "geometry_builder.h"
#include "material_builder.h"
#include "particle_generation.h"

#include <cmath>

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

    ParticleGeneration particle_generation(*this, config.at("particle_generation"));
    if (restart_config.restore_step_ == 0)
    {
        particle_generation.buildParticleGeneration(*this, config.at("particle_generation"));
        particle_generation.runRelaxation(config_manager_);
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
    SimulationBuilder::initializeAllBodyConfigs(config_manager_);
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
/**
 * @brief Return the SPH solver's current physical time.
 * @throws std::runtime_error If the solver has not been built.
 */
// NEW PUBLIC API: expose the solver's current physical time to Python.
// Unlike the number of Gymnasium steps, this is the solver's actual time.
Real SPHSimulation::getPhysicalTime()
{
    if (!sph_solver_ptr_)   // Check that the underlying SPH solver has been constructed.
    {
        // Fail explicitly instead of dereferencing a null solver pointer.
        throw std::runtime_error(
            "getPhysicalTime: simulation is not built.");
    }
    // Get the time-stepper reference and return its physical-time value.
    return sph_solver_ptr_->getTimeStepper().getPhysicalTime();
}

/**
 * @brief Compute the arithmetic mean velocity of a named real-particle body.
 * @param body_name Name of the target SPH RealBody.
 * @return Unweighted vector average over currently active real particles.
 * @throws std::runtime_error If uninitialized or if the body is empty.
 */
// NEW PUBLIC API: compute the unweighted mean velocity of active real particles.
// body_name identifies the SPH body (e.g. "WaterBody" in the smoke test).
Vecd SPHSimulation::getMeanVelocity(const std::string &body_name)
{
    // A valid measurement requires an initialized executable simulation and SPH system.
    if (!executable_simulation_state_ready_ || !sph_system_ptr_)
    {
        // Reject calls made before initialization to avoid accessing invalid data.
        throw std::runtime_error(
            "getMeanVelocity: simulation is not initialized.");
    }

    // Retrieve the specified real body from the existing SPH system.
    RealBody &body =
        // Resolve the named body from the existing SPHSystem.
        sph_system_ptr_->getBodyByName<RealBody>(body_name);

    // Access the particle data container without copying it.
    auto &particles = body.getBaseParticles();

    // Count the real particles used in the velocity average.
    const size_t n = particles.TotalRealParticles();

    // A mean is undefined if the body has zero active real particles.
    if (n == 0)
    {
        // Report the invalid state instead of dividing by zero.
        throw std::runtime_error(
            "getMeanVelocity: body contains no real particles.");
    }

    // Access the particle velocity array in the current simulation dimension.
    Vecd *velocity =
        particles.getVariableDataByName<Vecd>("Velocity");


    // Initialize every component of the velocity sum to zero.
    Vecd mean_velocity = Vecd::Zero();

    // Visit indices 0 ... n-1, representing the active real particles.
    for (size_t i = 0; i < n; ++i)
    {
        // Add velocity[i] componentwise to the running vector sum.
        mean_velocity += velocity[i];
    }

    // Convert n to Real to perform floating-point division; unweighted average.
    mean_velocity /= static_cast<Real>(n);

    // Return the vector; Python receives its components through pybind11.
    return mean_velocity;
}

/**
 * @brief Update a uniform horizontal acceleration command for one SPH body.
 * @param body_name Target SPH RealBody.
 * @param ax New horizontal control acceleration, in simulation units.
 * @details Adds m_i * (ax - previous_ax) to the x component of ForcePrior.
 * @warning Prototype validated only on the fixed-particle smoke-test case.
 *          Particle reordering/creation and competing force updates need review.
 */
// NEW PUBLIC API: apply a body-wide horizontal acceleration command.
// The method modifies the x-component of the existing ForcePrior array.
// Important: this implementation is only a prototype, not a general force model.
void SPHSimulation::setControlAcceleration(
    // body_name selects the target RealBody; ax is the requested x acceleration.
    const std::string &body_name, Real ax)
{
    // The force array can only be accessed after simulation initialization and SPH system.
    if (!executable_simulation_state_ready_ || !sph_system_ptr_)
    {
        // Raise an error if the solver/system is not ready.
        throw std::runtime_error(
            "setControlAcceleration: simulation is not initialized.");
    }

    // Reject NaN and +/-infinity, which would contaminate particle forces.
    if (!std::isfinite(ax))
    {
        // Invalid input values are reported as an argument error.
        throw std::invalid_argument(
            "setControlAcceleration: acceleration must be finite.");
    }

    // Locate the existing physical body to which the control applies.
    RealBody &body =
        sph_system_ptr_->getBodyByName<RealBody>(body_name);

    // Obtain a reference to this body's particle storage.
    auto &particles = body.getBaseParticles();

    // Read how many real particles currently participate in the simulation.
    const size_t n = particles.TotalRealParticles();

    // No control force can be applied if there are no real particles.
    if (n == 0)
    {
        // Abort this update rather than silently accepting an empty body.
        throw std::runtime_error(
            "setControlAcceleration: body has no real particles.");
    }

    // Request the particle mass array; mass[i] is the mass of particle i.
    Real *mass =
        // Get the registered "Mass" variable as an array of Real scalars.
        particles.getVariableDataByName<Real>("Mass");

    // Request the existing prior-force vector array.
    Vecd *force_prior =
        // Each force_prior[i] contains force components; force_prior[i][0] is the x component,force_prior[i][1] is the y component.
        particles.getVariableDataByName<Vecd>("ForcePrior");

    // Assume zero previously requested control for a body not yet in the map.
    Real old_ax = 0.0;

    // Search the per-body map holding the previous commanded acceleration.
    auto it = control_acceleration_x_.find(body_name);

    // If a previous command exists for this body, retrieve it.
    if (it != control_acceleration_x_.end())
    {
        // Use the stored previous x acceleration instead of zero.
        old_ax = it->second;
    }

    // Compute only the *change* in acceleration, not the full new value.
    // This avoids simply stacking the same control force on every call.
    const Real delta_ax = ax - old_ax;

    // Apply the incremental correction to every active real particle.
    for (size_t i = 0; i < n; ++i)
    {
        // Newton's second law: Delta Fx_i = mass_i * (ax - old_ax).
        // The += operator preserves other prior-force contributions already present.
        force_prior[i][0] += mass[i] * delta_ax;
    }

    // Remember the current command, so the next update uses the correct delta.
    control_acceleration_x_[body_name] = ax;
// LIMITATION: force retention and indexing are not guaranteed under particle
// sorting, insertion/removal, or other dynamics that rewrite ForcePrior.
// These cases need additional validation or a dedicated force-dynamics model.
}


} // namespace SPH
