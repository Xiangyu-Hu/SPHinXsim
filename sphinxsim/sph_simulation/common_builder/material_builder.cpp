#include "material_builder.h"

#include "composite_solid.h"
#include "fluid_dynamics_builder.h"
#include "sphinxsys.h"

namespace SPH
{
//=================================================================================================//
void MaterialBuilder::addMaterial(EntityManager &config_manager, SPHBody &sph_body, const json &config)
{
    addMatterMaterial(config_manager, sph_body, config);
    addOtherMaterialProperties(config_manager, sph_body, config);
}
//=================================================================================================//
StdVec<Real> MaterialBuilder::parseMixtureFractions(
    const ScalingConfig &scaling_config, const json &config)
{
    StdVec<Real> fractions;
    Real fraction_sum = Real(0);
    for (const auto &mf : config)
    {
        Real fraction = scaling_config.jsonToReal(mf, "Dimensionless");
        if (fraction < Real(0) || fraction > Real(1))
        {
            throw std::runtime_error(
                "MaterialBuilder::parseMixtureFractions: fractions values must be in [0, 1]");
        }
        fractions.push_back(fraction);
        fraction_sum += fraction;
    }

    if (ABS(fraction_sum - Real(1)) > Eps)
    {
        throw std::runtime_error(
            "MaterialBuilder::parseMixtureFractions: fractions must sum to 1");
    }
    return fractions;
}
//=================================================================================================//
void MaterialBuilder::addMatterMaterial(
    EntityManager &config_manager, SPHBody &sph_body, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    const std::string type = config.at("type").get<std::string>();

    if (type == "weakly_compressible_fluid")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real sound_speed = getWeaklyCompressibleSoundSpeed(config_manager);
        auto &material = sph_body.defineMatterMaterial<WeaklyCompressibleFluid>(density, sound_speed);
        config_manager.addEntity(sph_body.Name() + "WeaklyCompressibleFluid", &material);
        return;
    }

    if (type == "weakly_compressible_multi_species")
    {
        StdVec<std::pair<std::string, Real>> species_data;
        for (const auto &species_config : config.at("species"))
        {
            std::string species_name = species_config.at("name").get<std::string>();
            Real density = scaling_config.jsonToReal(species_config.at("density"), "Density");
            species_data.emplace_back(species_name, density);
        }
        Real sound_speed = getWeaklyCompressibleSoundSpeed(config_manager);
        auto &material = sph_body.defineMatterMaterial<WeaklyCompressibleMultiSpecies>(species_data, sound_speed);
        config_manager.addEntity(sph_body.Name() + "WeaklyCompressibleMultiSpecies", &material);
        return;
    }

    if (type == "weakly_compressible_multi_phase")
    {
        Real sound_speed = getWeaklyCompressibleSoundSpeed(config_manager);
        auto &material = sph_body.defineMatterMaterial<WeaklyCompressibleMultiPhase>(sound_speed);
        config_manager.addEntity(sph_body.Name() + "WeaklyCompressibleMultiPhase", &material);

        NamesAndDensities pure_phases = parseNamesAndDensities(scaling_config, config.at("pure_phases"));
        material.addPurePhases(pure_phases);

        if (config.contains("multi_species_phases"))
        {
            StdVec<std::pair<std::string, NamesAndDensities>> multi_species_phases;
            for (const auto &phase_config : config.at("multi_species_phases"))
            {
                std::string phase_name = phase_config.at("name").get<std::string>();
                NamesAndDensities species_data = parseNamesAndDensities(
                    scaling_config, phase_config.at("species"));
                multi_species_phases.emplace_back(phase_name, species_data);
            }
            material.addMultiSpeciesPhases(multi_species_phases);
        }

        material.setPhases();
        return;
    }

    if (type == "rigid_body")
    {
        auto &material = sph_body.defineMatterMaterial<Solid>();
        config_manager.addEntity(sph_body.Name() + "RigidBody", &material);
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
        
        DuctileDamageParameters damage_parameters;
        if (config.contains("ductile_damage"))
        {
            const auto &damage_config = config.at("ductile_damage");
            
            if (damage_config.contains("enabled"))
                damage_parameters.enabled_ =
                    damage_config.at("enabled").get<bool>();

            if (damage_config.contains("threshold_equivalent_plastic_strain"))
                damage_parameters.threshold_equivalent_plastic_strain_ =
                    scaling_config.jsonToReal(
                        damage_config.at("threshold_equivalent_plastic_strain"),
                        "Dimensionless");

            if (damage_config.contains("critical_equivalent_plastic_strain"))
                damage_parameters.critical_equivalent_plastic_strain_ =
                    scaling_config.jsonToReal(
                        damage_config.at("critical_equivalent_plastic_strain"),
                        "Dimensionless");

            if (damage_config.contains("critical_damage"))
                damage_parameters.critical_damage_ =
                    scaling_config.jsonToReal(
                        damage_config.at("critical_damage"),
                        "Dimensionless");
        }
        auto &material = sph_body.defineMatterMaterial<J2Plasticity>(
            density, sound_speed, youngs_modulus, poisson_ratio, yield_stress, hardening_modulus, damage_parameters);
        config_manager.addEntity(sph_body.Name() + "J2Plasticity", &material);
        return;
    }

    if (type == "plastic_continuum")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real youngs_modulus = scaling_config.jsonToReal(config.at("youngs_modulus"), "Stress");
        Real poisson_ratio = scaling_config.jsonToReal(config.at("poisson_ratio"), "Dimensionless");
        Real sound_speed = config.contains("sound_speed")
                               ? scaling_config.jsonToReal(config.at("sound_speed"), "Speed")
                               : sqrt(youngs_modulus /
                                      (density * Real(3) * (Real(1) - Real(2) * poisson_ratio)));
        Real friction_angle = scaling_config.jsonToReal(config.at("friction_angle"), "Dimensionless");
        Real cohesion = config.contains("cohesion")
                            ? scaling_config.jsonToReal(config.at("cohesion"), "Stress")
                            : Real(0);
        Real dilatancy_angle = config.contains("dilatancy_angle")
                                   ? scaling_config.jsonToReal(config.at("dilatancy_angle"), "Dimensionless")
                                   : Real(0);
        auto &material = sph_body.defineMatterMaterial<PlasticContinuum>(
            density, sound_speed, youngs_modulus, poisson_ratio,
            friction_angle, cohesion, dilatancy_angle);
        config_manager.addEntity(sph_body.Name() + "PlasticContinuum", &material);
        return;
    }

    if (type == "composite_solid")
    {
        Real density = scaling_config.jsonToReal(config.at("density"), "Density");
        Real poisson_ratio = scaling_config.jsonToReal(config.at("poisson_ratio"), "Dimensionless");
        Real youngs_active = scaling_config.jsonToReal(config.at("youngs_modulus_active"), "Stress");
        Real youngs_1 = scaling_config.jsonToReal(config.at("youngs_modulus_1"), "Stress");
        Real youngs_2 = scaling_config.jsonToReal(config.at("youngs_modulus_2"), "Stress");
        auto &material = sph_body.defineMatterMaterial<CompositeSolidMaterial>(
            density, youngs_active, youngs_1, youngs_2, poisson_ratio);
        config_manager.addEntity(sph_body.Name() + "CompositeSolid", &material);
        return;
    }

    throw std::runtime_error("MaterialBuilder::addMatterMaterial: unsupported material: " + type);
}
//=================================================================================================//
NamesAndDensities MaterialBuilder::parseNamesAndDensities(
    const ScalingConfig &scaling_config, const json &config)
{
    StdVec<std::pair<std::string, Real>> names_and_densities;
    for (const auto &species_config : config)
    {
        std::string name = species_config.at("name").get<std::string>();
        Real density = scaling_config.jsonToReal(species_config.at("density"), "Density");
        names_and_densities.emplace_back(name, density);
    }
    return names_and_densities;
}
//=================================================================================================//
void MaterialBuilder::addOtherMaterialProperties(
    EntityManager &config_manager, SPHBody &sph_body, const json &config)
{
    if (config.contains("viscosity"))
    {
        addViscosity(config_manager, sph_body, config.at("viscosity"));
    }

    if (config.contains("thermal_properties"))
    {
        addThermalProperties(config_manager, sph_body, config.at("thermal_properties"));
    }
}
//=================================================================================================//
void MaterialBuilder::addViscosity(EntityManager &config_manager, SPHBody &sph_body, const json &config)
{
    Real mu_ref = 0.0;
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");

    if (config.contains("Reynolds_number"))
    {
        mu_ref = 1.0 / scaling_config.jsonToReal(config.at("Reynolds_number"), "Dimensionless");
    }
    else
    {
        mu_ref = scaling_config.jsonToReal(config, "Viscosity");
    }
    auto &viscosity = sph_body.addMaterialProperty<Viscosity>(mu_ref);
    config_manager.addEntity(sph_body.Name() + "Viscosity", &viscosity);
}
//=================================================================================================//
void MaterialBuilder::addThermalProperties(
    EntityManager &config_manager, SPHBody &sph_body, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    if (!config.contains("thermal_boundary"))
    {
        auto &sph_body_config = config_manager.getEntity<SPHBodyConfig>(sph_body.Name());
        sph_body_config.setHasDynamics();

        Real d_coeff_ref = scaling_config.jsonToReal(
            config.at("thermal_conductivity"), "ThermalConductivity");
        Real cv = scaling_config.jsonToReal(
            config.at("volumetric_heat_capacity"), "VolumetricHeatCapacity");

        auto &diffusion = sph_body.addMaterialProperty<IsotropicDiffusion>(
            "Temperature", "Temperature", d_coeff_ref, cv);
        config_manager.addEntity(sph_body.Name() + "ThermalDiffusion", &diffusion);
        return;
    }
    else
    {
        ThermalBoundaryConfig boundary_config = parseThermalBoundaryConfig(config.at("thermal_boundary"));
        config_manager.emplaceEntity<ThermalBoundaryConfig>(sph_body.Name(), boundary_config);
        return;
    }
    throw std::runtime_error(
        "MaterialBuilder::addThermalProperties: unsupported thermal property configuration.");
}
//=================================================================================================//
ThermalBoundaryConfig MaterialBuilder::parseThermalBoundaryConfig(const json &config)
{
    ThermalBoundaryConfig thermal_boundary_config;
    thermal_boundary_config.boundary_type = config.get<std::string>();
    return thermal_boundary_config;
}
//=================================================================================================//
Real MaterialBuilder::getWeaklyCompressibleSoundSpeed(EntityManager &config_manager)
{
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    Real u_max_factor = fluid_solver_config.max_velocity_factor_;
    return 10.0 * u_max_factor; // 10 times of the maximum anticipated velocity
}
//=================================================================================================//
} // namespace SPH
