#include "sphinxsim_project.h"

#include "base_body.hpp"
#include "sphinxsys_entity.h"

namespace SPH
{
//=================================================================================================//
bool addExtraMaterial(EntityManager &config_manager, SPHBody &sph_body,
                      const json &config, const std::string &type)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");

    if (type == "composite_solid")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real poisson_ratio = scaling_config.jsonToReal(config.at("poisson_ratio"), "Dimensionless");
        Real youngs_active = scaling_config.jsonToReal(config.at("youngs_modulus_active"), "Stress");
        Real youngs_1 = scaling_config.jsonToReal(config.at("youngs_modulus_1"), "Stress");
        Real youngs_2 = scaling_config.jsonToReal(config.at("youngs_modulus_2"), "Stress");

        StdVec<Shape *> region_shapes;
        StdVec<int> region_ids;
        const json &region_config = config.at("material_id_regions");
        for (const auto &region : region_config.at("regions"))
        {
            std::string shape_name = region.at("shape").get<std::string>();
            region_shapes.push_back(&config_manager.getEntity<Shape>(shape_name));
            region_ids.push_back(region.at("id").get<int>());
        }
        int default_id = region_config.at("default_id").get<int>();

        auto &material = sph_body.defineMatterMaterial<CompositeSolidMaterial>(
            density, youngs_active, youngs_1, youngs_2, poisson_ratio,
            region_shapes, region_ids, default_id);
        config_manager.addEntity(sph_body.Name() + "CompositeSolid", &material);

        if (config.contains("active_strain"))
        {
            const json &wave_config = config.at("active_strain");
            auto &active_strain_config = *config_manager.emplaceEntity<
                ActiveStrainConfig>(sph_body.Name() + "ActiveStrainConfig");

            active_strain_config.wave_center_ =
                scaling_config.jsonToVecd(wave_config.at("center"), "Length");
            active_strain_config.wave_span_ =
                scaling_config.jsonToReal(wave_config.at("region_span"), "Length");
            active_strain_config.wave_core_ =
                scaling_config.jsonToReal(wave_config.at("core_thickness"), "Length");
            active_strain_config.amplitude_ =
                scaling_config.jsonToReal(wave_config.at("amplitude"), "Dimensionless");
            active_strain_config.frequency_ =
                scaling_config.jsonToReal(wave_config.at("frequency"), "Frequency");
            active_strain_config.wavelength_factor_ =
                scaling_config.jsonToReal(wave_config.at("wavelength_factor"), "Dimensionless");
            active_strain_config.start_time_ =
                scaling_config.jsonToReal(wave_config.at("start_time"), "Time");
        }

        auto &active_strain_property = sph_body.addMaterialProperty<ActiveModelSolid>(
            density, youngs_active, poisson_ratio);
        config_manager.addEntity(sph_body.Name() + "ActiveModelSolid", &active_strain_property);

        return true;
    }
    return false;
}
//=================================================================================================//
} // namespace SPH