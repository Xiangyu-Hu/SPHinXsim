#include "base_simulation_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
Vecd jsonToVecd(const nlohmann::json &arr)
{
    Vecd v = Vecd::Zero();
    const int dim = static_cast<int>(Vecd::RowsAtCompileTime);
    for (int i = 0; i < std::min(dim, static_cast<int>(arr.size())); ++i)
        v[i] = arr[i].get<Real>();
    return v;
}
//=================================================================================================//
#ifdef SPHINXSYS_2D
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>());
    Vec2d translation = jsonToVecd(config.at("translation"));
    return Transform(rotation, translation);
}
//=================================================================================================//
Rotation getRotationFromXAxis(const Vecd &direction)
{
    Real angle = std::atan2(direction[yAxis], direction[xAxis]);
    return Rotation(angle);
}
//=================================================================================================//
#else
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>(),
                      jsonToVecd(config.at("rotation_axis")));
    Vec3d translation = jsonToVecd(config.at("translation"));
    return Transform(rotation, translation);
}
//=================================================================================================//
Rotation getRotationFromXAxis(const Vecd &direction)
{
    Vec3d rotation_axis = Vec3d::UnitX().cross(direction);
    Real rotation_angle = std::acos(Vec3d::UnitX().dot(direction));
    return Rotation(rotation_angle, rotation_axis);
}
#endif
//=================================================================================================//
SimulationBuilder::SimulationBuilder()
    : material_builder_ptr_(std::make_unique<MaterialBuilder>()) {}
//=================================================================================================//
SimulationBuilder ::~SimulationBuilder() = default;
//=================================================================================================//
void SimulationBuilder::addFluidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &fb : config)
    {
        const std::string name = fb.at("name").get<std::string>();
        Shape &fluid_shape = config_manager.getEntityByName<Shape>(name);
        auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, fluid_body, fb.at("material"));
        if (fb.contains("particle_reserve_factor"))
        {
            ParticleBuffer<ReserveSizeFactor> inlet_buffer(
                fb.at("particle_reserve_factor").get<Real>());
            fluid_body.generateParticlesWithReserve<BaseParticles, Reload>(inlet_buffer, name);
        }
        else
        {
            fluid_body.generateParticles<BaseParticles, Reload>(name);
        }
    }
}
//=================================================================================================//
void SimulationBuilder::addContinuumBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &cb : config)
    {
        const std::string name = cb.at("name").get<std::string>();
        Shape &shape = config_manager.getEntityByName<Shape>(name);
        auto &continuum_body = sph_system.addBody<RealBody>(shape, name);
        material_builder_ptr_->addMaterial(config_manager, continuum_body, cb.at("material"));
        continuum_body.generateParticles<BaseParticles, Reload>(name);
    }
}
//=================================================================================================//
void SimulationBuilder::addSolidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &sb : config)
    {
        const std::string name = sb.at("name").get<std::string>();
        Shape &solid_shape = config_manager.getEntityByName<Shape>(name);
        auto &solid_body = sph_system.addBody<SolidBody>(solid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, solid_body, sb.at("material"));
        BaseParticles &reload_particles = solid_body.generateParticles<BaseParticles, Reload>(name);
        reload_particles.reloadExtraVariable<Vecd>("NormalDirection");
    }
}
//=================================================================================================//
void SimulationBuilder::addObservers(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    if (config.contains("observers"))
    {
        for (const auto &ob : config.at("observers"))
        {
            ObserverConfig observer_config;
            const std::string name = ob.at("name").get<std::string>();
            observer_config.name_ = name;
            observer_config.observed_body_ = ob.at("observed_body").get<std::string>();
            observer_config.observed_variable_ = parseVariableConfig(ob.at("variable"));

            StdVec<Vecd> positions;
            if (ob.contains("positions"))
            {
                for (const auto &p : ob.at("positions"))
                {
                    positions.push_back(jsonToVecd(p));
                }
            }

            ObserverBody &observer_body = sph_system.addBody<ObserverBody>(name);
            observer_body.generateParticles<ObserverParticles>(positions);
            config_manager.emplaceEntity<ObserverConfig>(name, observer_config);
        }
    }
}
//=================================================================================================//
void SimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    config_manager.emplaceEntity<
        SolverCommonConfig>("SolverCommonConfig", parseSolverCommonConfig(config));

    if (config.contains("restart"))
    {
        config_manager.emplaceEntity<RestartConfig>(
            "RestartConfig", parseRestartConfig(config.at("restart")));
    }
}
//=================================================================================================//
SolverCommonConfig SimulationBuilder::parseSolverCommonConfig(const json &config)
{
    SolverCommonConfig solver_common_config;
    if (config.contains("end_time"))
        solver_common_config.end_time_ = config.at("end_time").get<Real>();
    if (config.contains("output_interval"))
        solver_common_config.output_interval_ = config.at("output_interval").get<Real>();
    else
        solver_common_config.output_interval_ = solver_common_config.end_time_ / 100.0; // default to 100 output frames

    return solver_common_config;
}
//=================================================================================================//
RestartConfig SimulationBuilder::parseRestartConfig(const json &config)
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
std::string SimulationBuilder::getObserverRelationName(const ObserverConfig &observer_config)
{
    return observer_config.name_ + observer_config.observed_body_;
}
//=================================================================================================//
VariableConfig SimulationBuilder::parseVariableConfig(const json &config)
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

    throw std::runtime_error("SimulationBuilder::parseVariableConfig not supported variable type.");
}
//=================================================================================================//
void SimulationBuilder::addVariableToStateRecorder(
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

    throw std::runtime_error("SimulationBuilder::addVariableToStateRecorder not supported variable type.");
}
//=================================================================================================//
} // namespace SPH
