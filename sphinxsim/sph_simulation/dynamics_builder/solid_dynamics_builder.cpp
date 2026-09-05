#include "solid_dynamics_builder.h"

#include "region_shape_material_id.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void SolidDynamicsBuilder::buildMaterialIdAssignmentIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto solid_bodies = sph_system.collectBodies<SolidBody>();
    auto &material_id_assignment = main_methods.addParticleDynamicsGroup();

    for (const auto &solid_config : config.at("solid_bodies"))
    {
        const json &material_config = solid_config.at("material");
        if (!material_config.contains("material_id_regions"))
            continue;
        const json &region_config = material_config.at("material_id_regions");
        std::string body_name = solid_config.at("name").get<std::string>();
        SolidBody *target_body = nullptr;
        for (SolidBody *solid_body : solid_bodies)
        {
            if (solid_body->Name() == body_name)
                target_body = solid_body;
        }
        if (target_body == nullptr)
        {
            throw std::runtime_error("material id regions refer to an unknown solid body: " + body_name);
        }
        StdVec<Shape *> region_shapes;
        StdVec<int> region_ids;
        for (const auto &region : region_config.at("regions"))
        {
            std::string shape_name = region.at("shape").get<std::string>();
            region_shapes.push_back(&config_manager.getEntity<Shape>(shape_name));
            region_ids.push_back(region.at("id").get<int>());
        }
        int default_id = region_config.at("default_id").get<int>();
        material_id_assignment.add(&main_methods.addStateDynamics<RegionShapeMaterialId>(
            *target_body, region_shapes, region_ids, default_id));
    }

    if (material_id_assignment.hasDynamics())
    {
        sim.getInitializationPipeline().insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { material_id_assignment.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH