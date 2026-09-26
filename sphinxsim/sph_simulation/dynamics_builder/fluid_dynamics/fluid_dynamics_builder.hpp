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
template <template <typename...> class AcousticHalfStepType>
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticHalfStep(SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_half_step = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);

        if (fluid_body.template isMatterMaterial<WeaklyCompressibleFluid>())
        {
            acoustic_half_step.add(&addAcousticHalfStepForOneBody<
                                   AcousticHalfStepType, WeaklyCompressibleFluid>(
                sim, inner_relation, main_methods));
        }
        else
        {
            acoustic_half_step.add(&addAcousticHalfStepForOneBody<
                                   AcousticHalfStepType, WeaklyCompressibleMixture>(
                sim, inner_relation, main_methods));
        }
    }
    return acoustic_half_step;
}
//=================================================================================================//
template <template <typename...> class AcousticHalfStepType,
          class MatterMaterialType, class InnerRelationType>
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticHalfStepForOneBody(
    SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods)
{
    auto &fluid_body = inner_relation.getDynamicsIdentifier();
    using RiemannSolverType = RiemannSolver<MatterMaterialType, MatterMaterialType, TruncatedLinear>;

    if constexpr (std::is_base_of_v<AcousticStep2ndHalfTag, AcousticHalfStepType<>>)
    {
        using NoRiemannSolverType = RiemannSolver<NotUsed, MatterMaterialType, MatterMaterialType>;

        if (!fluid_body.template collectMaterialProperties<Viscosity>().empty())
        {
            auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
                AcousticHalfStepType, NoRiemannSolverType, LinearCorrectionCK>(inner_relation);

            addInteractionWithSolidBodies<Wall, NoRiemannSolverType, LinearCorrectionCK>(
                sim, complex_dynamics, fluid_body);
            addPressureForceOnSolidBodiesIfPresent<NoRiemannSolverType, LinearCorrectionCK>(
                sim, complex_dynamics, fluid_body);

            return complex_dynamics;
        }
        else
        {
            auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
                AcousticHalfStepType, RiemannSolverType, LinearCorrectionCK>(inner_relation);

            addInteractionWithSolidBodies<Wall, RiemannSolverType, LinearCorrectionCK>(
                sim, complex_dynamics, fluid_body);
            addPressureForceOnSolidBodiesIfPresent<RiemannSolverType, LinearCorrectionCK>(
                sim, complex_dynamics, fluid_body);

            return complex_dynamics;
        }
    }
    else
    {
        auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
            AcousticHalfStepType, RiemannSolverType, LinearCorrectionCK>(inner_relation);

        addInteractionWithSolidBodies<Wall, RiemannSolverType, LinearCorrectionCK>(
            sim, complex_dynamics, fluid_body);

        return complex_dynamics;
    }
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
template <typename... Parameters, class ViscosityForceType>
void FluidDynamicsBuilder::addViscousForceOnSolidBodiesIfPresent(
    SPHSimulation &sim, ViscosityForceType &viscous_force, SPHBodyConfig *fb)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    for (const auto &sb_tgt : solid_bodies_config)
    {
        if (sb_tgt->has_dynamics_)
        {
            std::string relation_name = sb_tgt->name_ + fb->name_;
            auto &contact_relation = sph_system.getRelationByName<
                Contact<Relation<SolidBody, FluidBody>>>(relation_name);
            viscous_force.template addGeneralPostInteraction<
                FSI::ViscousForceFromFluid, WithUpdate, Parameters...>(
                contact_relation);
        }
    }
}
//=================================================================================================//
template <typename... Parameters, class Acoustic2ndHalfStepType, class FluidIdentifier>
void FluidDynamicsBuilder::addPressureForceOnSolidBodiesIfPresent(
    SPHSimulation &sim, Acoustic2ndHalfStepType &acoustic_2nd_half_step,
    FluidIdentifier &fluid_identifier)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    std::string fluid_body_name = fluid_identifier.Name();
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    for (const auto &sb_tgt : solid_bodies_config)
    {
        if (sb_tgt->has_dynamics_)
        {
            std::string relation_name = sb_tgt->name_ + fluid_body_name;
            auto &contact_relation = sph_system.getRelationByName<
                Contact<Relation<SolidBody, FluidBody>>>(relation_name);
            acoustic_2nd_half_step.template addGeneralPostInteraction<
                FSI::PressureForceFromFluid, WithUpdate, Parameters...>(contact_relation);
        }
    }
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_DYNAMICS_BUILDER_HPP
