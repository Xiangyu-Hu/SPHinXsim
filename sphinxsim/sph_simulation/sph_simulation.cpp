#include "sph_simulation.h"

#include "fluid_simulation_builder.h"
namespace SPH
{
//=================================================================================================//
SPHSimulation::SPHSimulation(const fs::path &config_path) : config_path_(config_path) {}
//=================================================================================================//
void SPHSimulation::resetOutputRoot(const fs::path &output_root)
{
    IOEnvironment &io_env = IO::initEnvironment();
    io_env.resetOutputFolder((output_root / "output").string());
    io_env.resetRestartFolder((output_root / "restart").string());
    io_env.resetReloadFolder((output_root / "reload").string());
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
        BoundingBoxd domain_bounds(
            jsonToVecd(config.at("domain").at("lower_bound")),
            jsonToVecd(config.at("domain").at("upper_bound")));

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
void SPHSimulation::addShape(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    const std::string type = config.at("type").get<std::string>();

    if (type == "bounding_box")
    {
        Vecd lower_bound = jsonToVecd(config.at("lower_bound"));
        Vecd upper_bound = jsonToVecd(config.at("upper_bound"));
        entity_manager.addEntity<Shape>(
            name, &sph_system.addShape<GeometricShapeBox>(
                      BoundingBoxd(lower_bound, upper_bound), "BoundingBox"));
        return;
    }

    if (type == "container_box")
    {
        BoundingBoxd inner_box(jsonToVecd(config.at("inner_lower_bound")),
                               jsonToVecd(config.at("inner_upper_bound")));
        BoundingBoxd outer_box = inner_box.expand(config.at("thickness").get<Real>());
        auto &shape = sph_system.addShape<ComplexShape>("ContainerBox");
        shape.add<GeometricShapeBox>(outer_box);
        shape.subtract<GeometricShapeBox>(inner_box);
        entity_manager.addEntity<Shape>(name, &shape);
        return;
    }

#ifdef SPHINXSYS_2D
    if (type == "multipolygon")
    {
        MultiPolygon multi_polygon;
        for (const auto &plg : config.at("polygons"))
        {
            const std::string operation_name = plg.at("operation").get<std::string>();
            GeometricOps op = parseGeometricOp(operation_name);
            multi_polygon.addMultiPolygon(parseMultiPolygon(plg), op);
        }
        entity_manager.addEntity<Shape>(
            name, &sph_system.addShape<MultiPolygonShape>(multi_polygon, "MultiPolygon"));
        return;
    }
#endif

    throw std::runtime_error("SPHSimulation::addShape: unsupported shape type: " + type);
}
//=================================================================================================//
GeometricOps SPHSimulation::parseGeometricOp(const std::string &op_str)
{
    if (op_str == "union")
        return GeometricOps::add;
    if (op_str == "intersection")
        return GeometricOps::intersect;
    if (op_str == "subtraction")
        return GeometricOps::sub;

    throw std::runtime_error("SPHSimulation::parseGeometricOp: unsupported geometric operation: " + op_str);
}
//=================================================================================================//
#ifdef SPHINXSYS_2D
MultiPolygon SPHSimulation::parseMultiPolygon(const json &config)
{
    MultiPolygon multi_polygon;
    const std::string polygon_type = config.at("type").get<std::string>();
    if (polygon_type == "bounding_box")
    {
        Vecd lower_bound = jsonToVecd(config.at("lower_bound"));
        Vecd upper_bound = jsonToVecd(config.at("upper_bound"));
        multi_polygon.addBox(BoundingBoxd(lower_bound, upper_bound), GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "container_box")
    {
        BoundingBoxd inner_box(
            jsonToVecd(config.at("inner_lower_bound")), jsonToVecd(config.at("inner_upper_bound")));
        Real thickness = config.at("thickness").get<Real>();
        multi_polygon.addContainerBox(inner_box, thickness, GeometricOps::add);
        return multi_polygon;
    }

    throw std::runtime_error("SPHSimulation::addShape: unsupported polygon type: " + polygon_type);
}
#endif
//=================================================================================================//
void SPHSimulation::addMaterial(EntityManager &entity_manager, SPHBody &sph_body, const json &config)
{
    const std::string type_name = config.at("type").get<std::string>();

    if (type_name == "weakly_compressible_fluid")
    {
        Real density = config.at("density").get<Real>();
        Real sound_speed = config.at("sound_speed").get<Real>();
        auto &material = sph_body.defineMaterial<WeaklyCompressibleFluid>(density, sound_speed);
        entity_manager.addEntity(sph_body.getName() + material.MaterialType(), &material);
        return;
    }

    if (type_name == "rigid_body")
    {
        auto &material = sph_body.defineMaterial<Solid>();
        entity_manager.addEntity(sph_body.getName() + material.MaterialType(), &material);
        return;
    }

    throw std::runtime_error("SPHSimulation::addMaterial: unsupported material type: " + type_name);
}
//=================================================================================================//
void SPHSimulation::addFluidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    Shape &fluid_shape = entity_manager.getEntityByName<Shape>(name);
    auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape);
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
void SPHSimulation::addSolidBody(SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    Shape &wall_shape = entity_manager.getEntityByName<Shape>(name);
    auto &wall_body = sph_system.addBody<SolidBody>(wall_shape);
    addMaterial(entity_manager_, wall_body, config.at("material"));
    wall_body.generateParticles<BaseParticles, Lattice>();
    entity_manager_.addEntity(name, &wall_body);
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
    std::string simulation_type = config.at("simulation_type").get<std::string>();
    if (simulation_type == "fluid_dynamics")
    {
        simulation_builder_ptr_.createPtr<FluidSimulationBuilder>()->buildSimulation(*this, config);
        return;
    }
    throw std::runtime_error("SPHSimulation::buildSimulationFromJson: unsupported simulation type: " + simulation_type);
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

    if (config.contains("end_time"))
        end_time_ = config.at("end_time").get<Real>();
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

    executable_state_ready_ = true;
}
//=================================================================================================//
void SPHSimulation::run(Real end_time)
{
    if (!executable_state_ready_)
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
} // namespace SPH
