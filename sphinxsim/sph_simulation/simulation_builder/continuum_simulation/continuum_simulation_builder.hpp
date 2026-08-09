#ifndef CONTINUUM_SIMULATION_BUILDER_HPP
#define CONTINUUM_SIMULATION_BUILDER_HPP

#include "continuum_simulation_builder.h"

#include "all_continuum_dynamics_ck.h"
#include "fluid_dynamics_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
BaseDynamics<void> &ContinuumSimulationBuilder::addAcousticStep1stHalf(
    EntityManager &config_manager, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    std::string body_name = inner_relation.getSPHBody().Name();
    if (config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        using RiemannSolverType = RiemannSolver<GeneralContinuum, GeneralContinuum, NoLimiter>;
        return main_methods.template addInteractionDynamics<
            fluid_dynamics::AcousticStep1stHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        using RiemannSolverType = RiemannSolver<J2Plasticity, J2Plasticity, NoLimiter>;
        return main_methods.template addInteractionDynamics<
            fluid_dynamics::AcousticStep1stHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        using RiemannSolverType = RiemannSolver<PlasticContinuum, PlasticContinuum, TruncatedLinear>;
        return main_methods.template addInteractionDynamicsOneLevel<
                        continuum_dynamics::PlasticAcousticStep1stHalf,
                        RiemannSolverType, NoKernelCorrectionCK>(inner_relation)
            .template addPostContactInteraction<Wall, RiemannSolverType, NoKernelCorrectionCK>(contact_relation);
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addAcousticStep1stHalf: no supported material type found!");
}
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
BaseDynamics<void> &ContinuumSimulationBuilder::addAcousticStep2ndHalf(
    EntityManager &config_manager, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    std::string body_name = inner_relation.getSPHBody().Name();
    if (config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        using RiemannSolverType = RiemannSolver<GeneralContinuum, GeneralContinuum, NoLimiter>;
        return main_methods.template addInteractionDynamics<
            fluid_dynamics::AcousticStep2ndHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        using RiemannSolverType = RiemannSolver<J2Plasticity, J2Plasticity, NoLimiter>;
        return main_methods.template addInteractionDynamics<
            fluid_dynamics::AcousticStep2ndHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        using RiemannSolverType = RiemannSolver<PlasticContinuum, PlasticContinuum, TruncatedLinear>;
        auto &continuum_solver_parameters = config_manager.getEntity<
            ContinuumSolverParameters>("ContinuumSolverParameters");
        return main_methods.template addInteractionDynamicsOneLevel<
                        continuum_dynamics::PlasticAcousticStep2ndHalf,
                        RiemannSolverType, NoKernelCorrectionCK>(
            inner_relation, continuum_solver_parameters.plastic_riemann_dissipation_factor_)
            .template addPostContactInteraction<Wall, RiemannSolverType, NoKernelCorrectionCK>(contact_relation);
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addAcousticStep2ndHalf: no supported material type found!");
}
//=================================================================================================//
template <class InnerRelationType>
void ContinuumSimulationBuilder::buildShearForceIntegrationIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, InnerRelationType &inner_relation)
{
    auto &config_manager = sim.getConfigManager();
    auto add_shear_force_hook = [&](ParticleDynamicsGroup &continuum_shear_force)
    {
        auto *shear_force = &continuum_shear_force;
        auto *time_stepper = &sim.getSPHSolver().getTimeStepper();
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::BeforeMainPhysicalTimeStep, [shear_force, time_stepper]()
            { shear_force->exec(time_stepper->getGlobalTimeStepSize()); });
    };

    std::string body_name = inner_relation.getSPHBody().Name();
    if (config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        auto &continuum_solver_parameters = config_manager.getEntity<
            ContinuumSolverParameters>("ContinuumSolverParameters");
        auto &continuum_shear_force = main_methods.addParticleDynamicsGroup();
        continuum_shear_force
            .add(&main_methods.template addInteractionDynamics<
                  LinearGradient, Vecd>(inner_relation, "Velocity"))
            .add(&main_methods.template addInteractionDynamicsOneLevel<
                 continuum_dynamics::ShearIntegration, GeneralContinuum>(
                 inner_relation, continuum_solver_parameters.hourglass_factor_,
                 continuum_solver_parameters.shear_stress_damping_));

        add_shear_force_hook(continuum_shear_force);
        return;
    }

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        auto &continuum_solver_parameters = config_manager.getEntity<
            ContinuumSolverParameters>("ContinuumSolverParameters");
        auto &continuum_shear_force = main_methods.addParticleDynamicsGroup();
        continuum_shear_force
            .add(&main_methods.template addInteractionDynamics<
                  LinearGradient, Vecd>(inner_relation, "Velocity"))
            .add(&main_methods.template addInteractionDynamicsOneLevel<
                 continuum_dynamics::ShearIntegration, J2Plasticity>(
                 inner_relation, continuum_solver_parameters.hourglass_factor_,
                 continuum_solver_parameters.shear_stress_damping_));

        add_shear_force_hook(continuum_shear_force);
        return;
    }

    if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        return;
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::buildShearForceIntegrationIfPresent: no supported material type found!");
}
//=================================================================================================//
template <class InnerRelationType>
ParticleDynamicsGroup &ContinuumSimulationBuilder::addLinearCorrectionMatrix(
    EntityManager &config_manager, MainMethods &main_methods, InnerRelationType &inner_relation)
{
    auto &linear_correction_matrix = main_methods.addParticleDynamicsGroup();
    std::string body_name = inner_relation.getSPHBody().Name();
    if (!config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        auto &continuum_solver_parameters = config_manager.getEntity<
            ContinuumSolverParameters>("ContinuumSolverParameters");
        linear_correction_matrix.add(
            &main_methods.template addInteractionDynamicsWithUpdate<
                LinearCorrectionMatrix>(inner_relation, continuum_solver_parameters.linear_correction_matrix_coeff_));
    }
    return linear_correction_matrix;
}
//=================================================================================================//
template <class ContactRelationType>
void ContinuumSimulationBuilder::buildContactRepulsionIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, ContactRelationType &contact_relation)
{
    auto &config_manager = sim.getConfigManager();
    std::string body_name = contact_relation.getSPHBody().Name();
    if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        return;
    }

    if (!config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum") &&
        !config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        throw std::runtime_error(
            "ContinuumSimulationBuilder::buildContactRepulsionIfPresent: no supported material type found!");
    }

    auto &continuum_solver_parameters = config_manager.getEntity<
        ContinuumSolverParameters>("ContinuumSolverParameters");
    auto &contact_repulsion_factor = main_methods.addParticleDynamicsGroup();
    contact_repulsion_factor.add(
        &main_methods.template addInteractionDynamics<
            solid_dynamics::RepulsionFactor>(contact_relation));

    auto &contact_repulsion_force = main_methods.addParticleDynamicsGroup();
    contact_repulsion_force.add(
        &main_methods.template addInteractionDynamicsWithUpdate<
            solid_dynamics::RepulsionForceCK, Wall>(
            contact_relation, continuum_solver_parameters.contact_numerical_damping_));

    auto *repulsion_factor = &contact_repulsion_factor;
    auto *repulsion_force = &contact_repulsion_force;
    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [repulsion_factor]()
        { repulsion_factor->exec(); });

    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::BeforeMainPhysicalTimeStep, [repulsion_force]()
        { repulsion_force->exec(); });
    simulation_pipeline.insert_hook(
        SimulationHookPoint::AfterLinearCorrectionMatrix, [repulsion_factor]()
        { repulsion_factor->exec(); });
}
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
void ContinuumSimulationBuilder::buildDensityRegularizationIfPresent(
    SPHSimulation &sim, MainMethods &main_methods,
    SPHBody &continuum_body, InnerRelationType &inner_relation,
    ContactRelationType &contact_relation)
{
    EntityManager &config_manager = sim.getConfigManager();
    if (!config_manager.hasEntity<PlasticContinuum>(continuum_body.Name() + "PlasticContinuum"))
    {
        return;
    }

    auto &continuum_solver_parameters = config_manager.getEntity<
        ContinuumSolverParameters>("ContinuumSolverParameters");
    auto &density_regularization =
        FluidDynamicsBuilder::buildDensityRegularization<WeaklyCompressibleFluid>(
            sim, main_methods, inner_relation, contact_relation,
            continuum_solver_parameters.surface_type_);

    auto *density_regularization_ptr = &density_regularization;
    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialParticleIndicationTagging, [density_regularization_ptr]()
        { density_regularization_ptr->exec(); });

    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::ParticleIndicationTagging, [density_regularization_ptr]()
        { density_regularization_ptr->exec(); });
}
//=================================================================================================//
template <class InnerRelationType>
void ContinuumSimulationBuilder::buildStressDiffusionIfPresent(
    SPHSimulation &sim, MainMethods &main_methods,
    SPHBody &continuum_body, InnerRelationType &inner_relation,
    BodyStatesRecording &body_state_recorder)
{
    EntityManager &config_manager = sim.getConfigManager();
    if (!config_manager.hasEntity<PlasticContinuum>(continuum_body.Name() + "PlasticContinuum"))
    {
        return;
    }

    auto &stress_diffusion = main_methods.template addInteractionDynamics<
        continuum_dynamics::StressDiffusionCK>(inner_relation);

    body_state_recorder.addDerivedVariableRecording<
        StateDynamics<execution::ParallelPolicy, continuum_dynamics::VerticalStressCK>>(continuum_body);
    body_state_recorder.addDerivedVariableRecording<
        StateDynamics<execution::ParallelPolicy, continuum_dynamics::AccDeviatoricPlasticStrainCK>>(continuum_body);

    auto *stress_diffusion_ptr = &stress_diffusion;
    auto *time_stepper = &sim.getSPHSolver().getTimeStepper();
    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::BeforeMainPhysicalTimeStep, [stress_diffusion_ptr, time_stepper]()
        { stress_diffusion_ptr->exec(time_stepper->getGlobalTimeStepSize()); });
}
//=================================================================================================//
} // namespace SPH
#endif // CONTINUUM_SIMULATION_BUILDER_HPP
