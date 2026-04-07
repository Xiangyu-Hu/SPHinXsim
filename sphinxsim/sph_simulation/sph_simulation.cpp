#include "sph_simulation.h"

#include "continuum_simulation_builder.h"
#include "fluid_simulation_builder.h"
#include "geometry_builder.h"
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
SPHSystemConfig &SPHSimulation::getSPHSystemConfig(const json &config)
{
    if (!entity_manager_.hasEntity<SPHSystemConfig>("SPHSystemConfig"))
    {
        SPHSystemConfig system_config;
        Real particle_spacing = config.at("particle_spacing").get<Real>();
        int particle_boundary_buffer = config.at("particle_boundary_buffer").get<int>();
        Real boundary_width = particle_boundary_buffer * particle_spacing;
        BoundingBoxd domain_bounds = geometry_builder_.parseBoundingBox(config.at("domain"));
        system_config.system_domain_bounds_ = domain_bounds.expand(boundary_width);
        system_config.particle_spacing_ = particle_spacing;
        entity_manager_.emplaceEntity<SPHSystemConfig>("SPHSystemConfig", system_config);
    }
    return entity_manager_.getEntityByName<SPHSystemConfig>("SPHSystemConfig");
}
//=================================================================================================//
SPHSystem &SPHSimulation::defineSPHSystem(const json &config)
{
    SPHSystemConfig &system_config = getSPHSystemConfig(config);
    return *entity_manager_.emplaceEntity<SPHSystem>(
        "SPHSystem", system_config.system_domain_bounds_, system_config.particle_spacing_);
}
//=================================================================================================//
RelaxationSystem &SPHSimulation::defineRelaxationSystem(const json &config)
{
    SPHSystemConfig &system_config = getSPHSystemConfig(config);
    return *entity_manager_.emplaceEntity<RelaxationSystem>(
        "RelaxationSystem", system_config.system_domain_bounds_, system_config.particle_spacing_);
    ;
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
void SPHSimulation::addMaterial(EntityManager &entity_manager, SPHBody &sph_body, const json &config)
{
    const std::string type = config.at("type").get<std::string>();

    if (type == "weakly_compressible_fluid")
    {
        Real density = config.at("density").get<Real>();
        Real sound_speed = config.at("sound_speed").get<Real>();
        auto &material = sph_body.defineMaterial<WeaklyCompressibleFluid>(density, sound_speed);
        entity_manager.addEntity(sph_body.getName() + material.MaterialType(), &material);
        return;
    }

    if (type == "rigid_body")
    {
        auto &material = sph_body.defineMaterial<Solid>();
        entity_manager.addEntity(sph_body.getName() + material.MaterialType(), &material);
        return;
    }

    if (type == "j2_plasticity")
    {
        Real density = config.at("density").get<Real>();
        Real sound_speed = config.at("sound_speed").get<Real>();
        Real youngs_modulus = config.at("youngs_modulus").get<Real>();
        Real poisson_ratio = config.at("poisson_ratio").get<Real>();
        Real yield_stress = config.at("yield_stress").get<Real>();
        Real hardening_modulus = config.at("hardening_modulus").get<Real>();
        auto &material = sph_body.defineMaterial<J2Plasticity>(
            density, sound_speed, youngs_modulus, poisson_ratio, yield_stress, hardening_modulus);
        entity_manager.addEntity(sph_body.getName() + material.MaterialType(), &material);
        return;
    }

    throw std::runtime_error("SPHSimulation::addMaterial: unsupported material type: " + type);
}
//=================================================================================================//
void SPHSimulation::addRelaxationBody(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    Shape &shape = entity_manager.getEntityByName<Shape>(name);
    auto &relaxation_body = relaxation_system.addBody<RealBody>(shape, name);
    relaxation_body.generateParticles<BaseParticles, Lattice>();
    LevelSetShape &level_set_shape = relaxation_body.defineBodyLevelSetShape(par_ck, 2.0).writeLevelSet();
    entity_manager.addEntity(name, &level_set_shape);
    entity_manager.addEntity(name, &relaxation_body);
}
//=================================================================================================//
void SPHSimulation::addFluidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    Shape &fluid_shape = entity_manager.getEntityByName<Shape>(name);
    auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
    addMaterial(entity_manager_, fluid_body, config.at("material"));
    if (config.contains("particle_reserve_factor"))
    {
        ParticleBuffer<ReserveSizeFactor> inlet_buffer(
            config.at("particle_reserve_factor").get<Real>());
        fluid_body.generateParticlesWithReserve<BaseParticles, Lattice>(inlet_buffer);
    }
    else
    {
        fluid_body.generateParticles<BaseParticles, Lattice>();
    }
    entity_manager_.addEntity(name, &fluid_body);
}
//=================================================================================================//
void SPHSimulation::addContinuumBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    Shape &shape = entity_manager.getEntityByName<Shape>(name);
    auto &continuum_body = sph_system.addBody<RealBody>(shape, name);
    addMaterial(entity_manager_, continuum_body, config.at("material"));
    continuum_body.generateParticles<BaseParticles, Lattice>();
    entity_manager_.addEntity(name, &continuum_body);
}
//=================================================================================================//
void SPHSimulation::addSolidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    Shape &solid_shape = entity_manager.getEntityByName<Shape>(name);
    auto &solid_body = sph_system.addBody<SolidBody>(solid_shape, name);
    addMaterial(entity_manager_, solid_body, config.at("material"));
    if (config.contains("particle_reload"))
    {
        BaseParticles &reload_particles = solid_body.generateParticles<BaseParticles, Reload>(name);
        parseParticleReload(config.at("particle_reload"), reload_particles);
    }
    else
    {
        solid_body.generateParticles<BaseParticles, Lattice>();
    }
    entity_manager_.addEntity(name, &solid_body);
}
//=================================================================================================//
void SPHSimulation::parseParticleReload(const json &config, BaseParticles &reload_particles)
{
    if (config.contains("reload_variables"))
    {
        for (const auto &var : config.at("reload_variables"))
        {
            if (var == "NormalDirection")
            {
                reload_particles.reloadExtraVariable<Vecd>("NormalDirection");
            }
            else
            {
                throw std::runtime_error(
                    "SPHSimulation::parseParticleReload: unsupported reload variable: " + var.get<std::string>());
            }
        }
    }
}
//=================================================================================================//
void SPHSimulation::addObserver(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    StdVec<Vecd> positions;
    if (config.contains("positions"))
    {
        for (const auto &p : config.at("positions"))
            positions.push_back(jsonToVecd(p));
    }
    else if (config.contains("position"))
    {
        positions.push_back(jsonToVecd(config.at("position")));
    }

    ObserverBody &observer_body = sph_system.addBody<ObserverBody>(name);
    observer_body.generateParticles<ObserverParticles>(positions);
    entity_manager_.addEntity(name, &observer_body);
}
//=================================================================================================//
void SPHSimulation::buildSimulationFromJson(const json &config)
{
    geometry_builder_.addGeometries(entity_manager_, config);

    if (config.contains("particle_relaxation"))
    {
        defineRelaxationSystem(config);
        entity_manager_.emplaceEntity<ParticleRelaxationBuilder>("ParticleRelaxation")
            ->buildSimulation(*this, config.at("particle_relaxation"));
        executable_particle_relaxation_ready_ = true;
        runParticleRelaxation();
    }

    if (config.contains("simulation_type"))
    {
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
void SPHSimulation::run(Real end_time)
{
    if (!executable_simulation_state_ready_)
    {
        std::cerr << "SPHSimulation::run: Simulation is not initialized. "
                     "Call initializeSimulation() before run.\n";
        return;
    }

    while (!sph_solver_ptr_->getTimeStepper().isEndTime(end_time))
    {
        for (auto &step : simulation_pipeline_.main_steps)
        {
            step(); // each step touches all cells internally
        }
    }
}
//=================================================================================================//
void SPHSimulation::runParticleRelaxation()
{
    if (!executable_particle_relaxation_ready_)
    {
        std::cerr << "SPHSimulation::runParticleRelaxation: Simulation is not initialized. "
                     "Call initializeSimulation() before run.\n";
        return;
    }

    ParticleRelaxationBuilder &relaxation_builder =
        entity_manager_.getEntityByName<ParticleRelaxationBuilder>("ParticleRelaxation");
    relaxation_builder.runRelaxation();
}
//=================================================================================================//
} // namespace SPH
