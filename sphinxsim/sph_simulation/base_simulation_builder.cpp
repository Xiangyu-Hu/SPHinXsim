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
#else
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>(),
                      jsonToVecd(config.at("rotation_axis")));
    Vec3d translation = jsonToVecd(config.at("translation"));
    return Transform(rotation, translation);
}
#endif
//=================================================================================================//
void SimulationBuilder::addFluidBodies(
    SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    MaterialBuilder &material_builder =
        entity_manager.getEntityByName<MaterialBuilder>("MaterialBuilder");
    for (const auto &fb : config.at("fluid_bodies"))
    {
        const std::string name = fb.at("name").get<std::string>();
        Shape &fluid_shape = entity_manager.getEntityByName<Shape>(name);
        auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
        material_builder.addMaterial(entity_manager, fluid_body, fb.at("material"));
        if (fb.contains("particle_reserve_factor"))
        {
            ParticleBuffer<ReserveSizeFactor> inlet_buffer(
                fb.at("particle_reserve_factor").get<Real>());
            fluid_body.generateParticlesWithReserve<BaseParticles, Lattice>(inlet_buffer);
        }
        else
        {
            fluid_body.generateParticles<BaseParticles, Lattice>();
        }
        entity_manager.addEntity(name, &fluid_body);
    }
}
//=================================================================================================//
void SimulationBuilder::addContinuumBodies(
    SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    MaterialBuilder &material_builder =
        entity_manager.getEntityByName<MaterialBuilder>("MaterialBuilder");
    for (const auto &cb : config.at("continuum_bodies"))
    {
        const std::string name = cb.at("name").get<std::string>();
        Shape &shape = entity_manager.getEntityByName<Shape>(name);
        auto &continuum_body = sph_system.addBody<RealBody>(shape, name);
        material_builder.addMaterial(entity_manager, continuum_body, cb.at("material"));
        continuum_body.generateParticles<BaseParticles, Lattice>();
        entity_manager.addEntity(name, &continuum_body);
    }
}
//=================================================================================================//
void SimulationBuilder::addSolidBodies(
    SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    MaterialBuilder &material_builder =
        entity_manager.getEntityByName<MaterialBuilder>("MaterialBuilder");
    for (const auto &sb : config.at("solid_bodies"))
    {
        const std::string name = sb.at("name").get<std::string>();
        Shape &solid_shape = entity_manager.getEntityByName<Shape>(name);
        auto &solid_body = sph_system.addBody<SolidBody>(solid_shape, name);
        material_builder.addMaterial(entity_manager, solid_body, sb.at("material"));
        if (sb.contains("particle_reload"))
        {
            BaseParticles &reload_particles = solid_body.generateParticles<BaseParticles, Reload>(name);
            parseParticleReload(sb.at("particle_reload"), reload_particles);
        }
        else
        {
            solid_body.generateParticles<BaseParticles, Lattice>();
        }
        entity_manager.addEntity(name, &solid_body);
    }
}
//=================================================================================================//
void SimulationBuilder::addObservers(
    SPHSystem &sph_system, EntityManager &entity_manager, const json &config)
{
    for (const auto &ob : config.at("observers"))
    {
        const std::string name = ob.at("name").get<std::string>();
        StdVec<Vecd> positions;
        if (ob.contains("positions"))
        {
            for (const auto &p : ob.at("positions"))
                positions.push_back(jsonToVecd(p));
        }
        else if (ob.contains("position"))
        {
            positions.push_back(jsonToVecd(ob.at("position")));
        }

        ObserverBody &observer_body = sph_system.addBody<ObserverBody>(name);
        observer_body.generateParticles<ObserverParticles>(positions);
        entity_manager.addEntity(name, &observer_body);
    }
}
//=================================================================================================//
void SimulationBuilder::parseParticleReload(const json &config, BaseParticles &reload_particles)
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
} // namespace SPH
