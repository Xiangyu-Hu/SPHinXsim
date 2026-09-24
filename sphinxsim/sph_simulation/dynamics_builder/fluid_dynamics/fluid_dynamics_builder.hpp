#ifndef FLUID_DYNAMICS_BUILDER_HPP
#define FLUID_DYNAMICS_BUILDER_HPP

#include "fluid_dynamics_builder.h"
#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
template <class DynamicsIdentifier>
void FluidDynamicsBuilder::assignWeaklyCompressibleMultiSpecies(
    ParticleDynamicsGroup &particle_dynamics_group, DynamicsIdentifier &identifier,
    WeaklyCompressibleMultiSpecies &mixture, ScalingConfig &scaling_config,
    MainMethods &main_methods, const json &config)
{
    if (config.contains("mass_fractions"))
    {
        StdVec<Real> mass_fractions = MaterialBuilder::parseMixtureFractions(
            scaling_config, config.at("mass_fractions"));
        particle_dynamics_group.add(
            &main_methods.template addStateDynamics<
                VariableAssignment,
                ConstantMixtureFraction<WeaklyCompressibleMultiSpecies>>(
                identifier, mixture, mass_fractions));

        particle_dynamics_group.add(
            &main_methods.template addStateDynamics<
                VariableAssignment,
                UpdateReferenceDensity<WeaklyCompressibleMultiSpecies>>(
                identifier, mixture));
    }
}
//=================================================================================================//
template <class DynamicsIdentifier>
void FluidDynamicsBuilder::assignWeaklyCompressibleMultiPhase(
    ParticleDynamicsGroup &particle_dynamics_group, DynamicsIdentifier &identifier,
    WeaklyCompressibleMultiPhase &mixture, ScalingConfig &scaling_config,
    MainMethods &main_methods, const json &config)
{
    if (config.contains("multi_species_phases"))
    {
        for (const auto &phase : config.at("multi_species_phases"))
        {
            std::string phase_name = phase.at("phase_name").get<std::string>();
            auto &multi_species_phase = mixture.getMultiSpeciesPhaseByName(phase_name);
            StdVec<Real> mass_fractions = MaterialBuilder::parseMixtureFractions(
                scaling_config, phase.at("mass_fractions"));

            particle_dynamics_group.add(
                &main_methods.template addStateDynamics<
                    VariableAssignment,
                    ConstantMixtureFraction<WeaklyCompressibleMultiSpecies>>(
                    identifier, multi_species_phase, mass_fractions));
        }
    }

    if (config.contains("volume_fractions"))
    {
        StdVec<Real> volume_fractions = MaterialBuilder::parseMixtureFractions(
            scaling_config, config.at("volume_fractions"));
        particle_dynamics_group.add(
            &main_methods.template addStateDynamics<
                VariableAssignment,
                ConstantMixtureFraction<WeaklyCompressibleMultiPhase>>(
                identifier, mixture, volume_fractions));
        particle_dynamics_group.add(
            &main_methods.template addStateDynamics<
                VariableAssignment,
                UpdateReferenceDensity<WeaklyCompressibleMultiPhase>>(
                identifier, mixture));
    }
}
//=================================================================================================//
template <class DynamicsIdentifier>
void FluidDynamicsBuilder::assignSupplementaryConditions(
    DynamicsIdentifier &identifier, ParticleDynamicsGroup &particle_dynamics_group,
    EntityManager &config_manager, MainMethods &main_methods, const json &config)
{
    const std::string &body_name = identifier.getSPHBody().Name();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");

    if (config_manager.hasEntity<WeaklyCompressibleMultiPhase>(
            body_name + "WeaklyCompressibleMultiPhase"))
    {
        auto &mixture = config_manager.getEntity<WeaklyCompressibleMultiPhase>(
            body_name + "WeaklyCompressibleMultiPhase");
        assignWeaklyCompressibleMultiPhase(
            particle_dynamics_group, identifier,
            mixture, scaling_config, main_methods, config);
    }

    if (config_manager.hasEntity<WeaklyCompressibleMultiSpecies>(
            body_name + "WeaklyCompressibleMultiSpecies"))
    {
        auto &mixture = config_manager.getEntity<WeaklyCompressibleMultiSpecies>(
            body_name + "WeaklyCompressibleMultiSpecies");
        assignWeaklyCompressibleMultiSpecies(
            particle_dynamics_group, identifier,
            mixture, scaling_config, main_methods, config);
    }

    if (config_manager.hasEntity<IsotropicDiffusion>(
            body_name + "ThermalDiffusion"))
    {
        if (config.contains("temperature"))
        {
            Real temperature = scaling_config.jsonToReal(
                config.at("temperature"), "Temperature");
            particle_dynamics_group.add(
                &main_methods.template addStateDynamics<
                    VariableAssignment, ConstantValue<Real>>(
                    identifier, "Temperature", temperature));
        }
    }
}
//=================================================================================================//
template <class FluidType, class FluidBodyType>
BaseDynamics<void> &FluidDynamicsBuilder::addDensityRegularizationForOneBody(
    MainMethods &main_methods, FluidBodyType &fluid_body, const std::string &surface_type)
{
    if (surface_type == "confined")
    {
        return main_methods.template addStateDynamics<
            DensityRegularization, FluidType, Internal>(fluid_body);
    }

    if (surface_type == "free_surface")
    {
        return main_methods.template addStateDynamics<
            DensityRegularization, FluidType, FreeSurface>(fluid_body);
    }

    if (surface_type == "open_boundary")
    {
        return main_methods.template addStateDynamics<
            DensityRegularization, FluidType, Internal, ExcludeBufferParticles>(fluid_body);
    }

    if (surface_type == "free_stream")
    {
        return main_methods.template addStateDynamics<
            DensityRegularization, FluidType, FreeStream>(fluid_body);
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::addDensityRegularizationForOneBody: no supported surface type found!");
}
//=================================================================================================//
template <template <typename...> class AcousticHalfStepForOneBodyType, class InnerRelationType>
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticHalfStepForOneBody(
    SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_body = inner_relation.getDynamicsIdentifier();
    std::string body_name = fluid_body.Name();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_body.template isMatterMaterial<WeaklyCompressibleFluid>())
    {
        using RiemannSolverType =
            RiemannSolver<WeaklyCompressibleFluid, WeaklyCompressibleFluid, TruncatedLinear>;
        std::string kernel_correction = fluid_solver_config.kernel_correction_;

        if (kernel_correction == "none")
        {
            auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
                AcousticHalfStepForOneBodyType, RiemannSolverType, NoKernelCorrectionCK>(inner_relation);

            addInteractionWithSolidBodies<Wall, RiemannSolverType, NoKernelCorrectionCK>(
                sim, complex_dynamics, fluid_body);

            return complex_dynamics;
        }
        else
        {
            auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
                AcousticHalfStepForOneBodyType, RiemannSolverType, LinearCorrectionCK>(inner_relation);

            addInteractionWithSolidBodies<Wall, RiemannSolverType, LinearCorrectionCK>(
                sim, complex_dynamics, fluid_body);
            return complex_dynamics;
        }
    }

    if (fluid_body.template isMatterMaterial<WeaklyCompressibleMixture>())
    {
        using RiemannSolverType =
            RiemannSolver<WeaklyCompressibleMixture, WeaklyCompressibleMixture, TruncatedLinear>;

        auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
            AcousticHalfStepForOneBodyType, RiemannSolverType, LinearCorrectionCK>(inner_relation);

        addInteractionWithSolidBodies<Wall, RiemannSolverType, LinearCorrectionCK>(
            sim, complex_dynamics, fluid_body);

        return complex_dynamics;
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::addAcousticHalfStepForOneBody: no supported material type found!");
}
//=================================================================================================//
template <typename... Parameters, class MainInteractionType, class FluidIdentifier>
void FluidDynamicsBuilder::addInteractionWithSolidBodies(
    SPHSimulation &sim, MainInteractionType &main_interaction, FluidIdentifier &fluid_identifier)
{
    auto &config_manager = sim.getConfigManager();
    auto &sph_system = sim.getSPHSystem();

    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    for (const auto &sb_tgt : solid_bodies_config)
    {
        std::string relation_name = fluid_identifier.Name() + sb_tgt->name_;
        auto &contact_relation = sph_system.getRelationByName<
            Contact<Relation<FluidIdentifier, SolidBody>>>(relation_name);
        main_interaction.template addPostContactInteraction<Parameters...>(contact_relation);
    }
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_DYNAMICS_BUILDER_HPP
