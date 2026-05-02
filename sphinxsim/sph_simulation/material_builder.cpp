#include "material_builder.h"

#include "sphinxsys.h"

namespace SPH
{
//=================================================================================================//
void MaterialBuilder::addMaterial(EntityManager &config_manager, SPHBody &sph_body, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    const std::string type = config.at("type").get<std::string>();

    if (type == "weakly_compressible_fluid")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real sound_speed = scaling_config.jsonToReal(config.at("sound_speed"), "Speed");
        if (config.contains("viscosity"))
        {
            Real viscosity = scaling_config.jsonToReal(config.at("viscosity"), "Viscosity");
            auto &material = sph_body.defineClosure<WeaklyCompressibleFluid, Viscosity>(
                ConstructArgs(density, sound_speed), viscosity);
            config_manager.addEntity(sph_body.getName() + "WeaklyCompressibleFluid", &material);
            config_manager.addEntity(sph_body.getName() + "Viscosity", DynamicCast<Viscosity>(this, &material));
            return;
        }
        auto &material = sph_body.defineMaterial<WeaklyCompressibleFluid>(density, sound_speed);
        config_manager.addEntity(sph_body.getName() + "WeaklyCompressibleFluid", &material);
        return;
    }

    if (type == "rigid_body")
    {
        auto &material = sph_body.defineMaterial<Solid>();
        config_manager.addEntity(sph_body.getName() + "RigidBody", &material);
        return;
    }

    if (type == "j2_plasticity")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real sound_speed = scaling_config.jsonToReal(config.at("sound_speed"), "Speed");
        Real youngs_modulus = scaling_config.jsonToReal(config.at("youngs_modulus"), "Stress");
        Real poisson_ratio = scaling_config.jsonToReal(config.at("poisson_ratio"), "Dimensionless");
        Real yield_stress = scaling_config.jsonToReal(config.at("yield_stress"), "Stress");
        Real hardening_modulus = scaling_config.jsonToReal(config.at("hardening_modulus"), "Stress");
        auto &material = sph_body.defineMaterial<J2Plasticity>(
            density, sound_speed, youngs_modulus, poisson_ratio, yield_stress, hardening_modulus);
        config_manager.addEntity(sph_body.getName() + "J2Plasticity", &material);
        return;
    }

    if (type == "general_continuum")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real sound_speed = scaling_config.jsonToReal(config.at("sound_speed"), "Speed");
        Real youngs_modulus = scaling_config.jsonToReal(config.at("youngs_modulus"), "Stress");
        Real poisson_ratio = scaling_config.jsonToReal(config.at("poisson_ratio"), "Dimensionless");
        auto &material = sph_body.defineMaterial<GeneralContinuum>(
            density, sound_speed, youngs_modulus, poisson_ratio);
        config_manager.addEntity(sph_body.getName() + "GeneralContinuum", &material);
        return;
    }

    throw std::runtime_error("MaterialBuilder::addMaterial: unsupported material: " + type);
}
//=================================================================================================//
} // namespace SPH
