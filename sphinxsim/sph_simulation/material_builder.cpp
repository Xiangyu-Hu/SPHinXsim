#include "material_builder.h"

#include "sphinxsys.h"

namespace SPH
{
//=================================================================================================//
void MaterialBuilder::addMaterial(EntityManager &entity_manager, SPHBody &sph_body, const json &config)
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

    throw std::runtime_error("MaterialBuilder::addMaterial: unsupported material: " + type);
}
//=================================================================================================//
} // namespace SPH
