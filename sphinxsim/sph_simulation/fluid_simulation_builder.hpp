#ifndef FLUID_SIMULATION_BUILDER_HPP
#define FLUID_SIMULATION_BUILDER_HPP

#include "fluid_simulation_builder.h"

#include "geometry_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType, class ContactRelationType>
BaseDynamics<void> &FluidSimulationBuilder::addDensitySummationAndRegularization(
    EntityManager &config_manager, MethodContainerType &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &fluid_density_regularization =
        main_methods.template addInteractionDynamics<fluid_dynamics::DensitySummationCK>(inner_relation)
            .addPostContactInteraction(contact_relation);

    auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_solver_config.surface_type_ == "confined")
    {
        fluid_density_regularization.template addPostStateDynamics<
            fluid_dynamics::DensityRegularization, Internal>(inner_relation.getSPHBody());
        return fluid_density_regularization;
    }

    if (fluid_solver_config.surface_type_ == "free_surface")
    {
        fluid_density_regularization.template addPostStateDynamics<
            fluid_dynamics::DensityRegularization, FreeSurface>(inner_relation.getSPHBody());
        return fluid_density_regularization;
    }

    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        fluid_density_regularization.template addPostStateDynamics<
            fluid_dynamics::DensityRegularization, Internal, ExcludeBufferParticles>(
            inner_relation.getSPHBody());
        return fluid_density_regularization;
    }

    throw std::runtime_error(
        "FluidSimulationBuilder::addDensitySummationAndRegularization: no supported flow type found!");
}
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType, class ContactRelationType>
void FluidSimulationBuilder::buildTransportVelocityFormulationIfNotFreeSurface(
    SPHSimulation &sim, MethodContainerType &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    EntityManager &config_manager = sim.getConfigManager();

    auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.surface_type_ != "free_surface")
    {
        auto &transport_velocity_correction =
            main_methods.template addInteractionDynamics<
                            KernelGradientIntegral, LinearCorrectionCK>(inner_relation)
                .template addPostContactInteraction<Boundary, NoKernelCorrectionCK>(contact_relation);

        addTransportVelocityCorrection(
            transport_velocity_correction, inner_relation.getSPHBody(), fluid_solver_config);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterAdvectionStepSetup, [&]()
            { transport_velocity_correction.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterAdvectionStepSetup, [&]()
            { transport_velocity_correction.exec(); });
    }
}
//=================================================================================================//
template <class KernelGradientIntegralType>
void FluidSimulationBuilder::addTransportVelocityCorrection(
    KernelGradientIntegralType &kernel_gradient_integral,
    SPHBody &sph_body, FluidSolverConfig &fluid_solver_config)
{
    if (fluid_solver_config.surface_type_ == "confined")
    {
        kernel_gradient_integral.template addPostStateDynamics<
            fluid_dynamics::TransportVelocityCorrectionCK, TruncatedLinear>(sph_body);
        return;
    }

    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        kernel_gradient_integral.template addPostStateDynamics<
            fluid_dynamics::TransportVelocityCorrectionCK, TruncatedLinear, BulkParticles>(sph_body);
        return;
    }
    throw std::runtime_error(
        "FluidSimulationBuilder::addTransportVelocityCorrection: no supported flow type found!");
}
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType, class ContactRelationType>
void FluidSimulationBuilder::buildViscousForceIfPresent(
    SPHSimulation &sim, MethodContainerType &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    EntityManager &config_manager = sim.getConfigManager();

    SPHBody &sph_body = inner_relation.getSPHBody();
    if (config_manager.hasEntity<Viscosity>(sph_body.getName() + "Viscosity"))
    {
        auto &viscous_force =
            main_methods.template addInteractionDynamicsWithUpdate<
                            fluid_dynamics::ViscousForceCK, Viscosity, NoKernelCorrectionCK>(inner_relation)
                .template addPostContactInteraction<Wall, Viscosity, NoKernelCorrectionCK>(contact_relation);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterAdvectionStepSetup, [&]()
            { viscous_force.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterAdvectionStepSetup, [&]()
            { viscous_force.exec(); });
    }
}
//=================================================================================================//
template <class MethodContainerType>
void FluidSimulationBuilder::buildBoundaryConditionsIfPresent(
    SPHSimulation &sim, MethodContainerType &main_methods, const json &config)
{
    if (config.contains("fluid_boundary_conditions"))
    {
        for (const auto &bd : config.at("fluid_boundary_conditions"))
        {
            addBoundaryCondition(sim, main_methods, bd);
        }
    }
}
//=================================================================================================//
template <class MethodContainerType>
void FluidSimulationBuilder::addBoundaryCondition(
    SPHSimulation &sim, MethodContainerType &main_methods, const json &config)
{
    StagePipeline<InitializationHookPoint> &initialization_pipeline = sim.getInitializationPipeline();
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();
    EntityManager &config_manager = sim.getConfigManager();
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();

    const std::string body_name = config.at("body_name").get<std::string>();
    FluidBody &fluid_body = sim.getSPHSystem().getBodyByName<FluidBody>(body_name);
    AlignedBox &aligned_box = config_manager.getEntityByName<AlignedBox>(
        config.at("aligned_box").get<std::string>());
    const std::string type = config.at("type").get<std::string>();

    if (type == "emitter")
    { // must be aligned box for emitter
        auto &emitter = fluid_body.addBodyPart<AlignedBoxByParticle>(aligned_box);
        auto &inflow_condition = main_methods.template addStateDynamics<
            fluid_dynamics::EmitterInflowConditionCK, ConstantInflowSpeed>(
            emitter, config.at("inflow_speed").get<Real>());
        auto &injection = main_methods.template addStateDynamics<
            fluid_dynamics::EmitterInflowInjectionCK>(emitter);

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            { inflow_condition.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { injection.exec(); });

        return;
    }

    if (type == "bi_directional")
    {
        auto &aligned_box_by_cell = fluid_body.addBodyPart<AlignedBoxByCell>(aligned_box);
        auto &bi_directional_bd = createBiDirectionBoundary(
            aligned_box_by_cell, config_manager, main_methods, config);

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialParticleIndicationTagging, [&]()
            { bi_directional_bd.tagBufferParticles(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            {   
                Real dt = time_stepper.getGlobalTimeStepSize();
                bi_directional_bd.applyBoundaryCondition(dt); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { bi_directional_bd.injectParticles(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleDeletionTagging, [&]()
            { bi_directional_bd.indicateOutFlowParticles(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleIndicationTagging, [&]()
            { bi_directional_bd.tagBufferParticles(); });

        auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");
        if (fluid_solver_config.particle_deletion_ == false)
        {
            auto &particle_deletion = main_methods.template addStateDynamics<
                fluid_dynamics::OutflowParticleDeletion>(fluid_body);
            fluid_solver_config.particle_deletion_ = true; // enable particle deletion

            simulation_pipeline.insert_hook(
                SimulationHookPoint::ParticleDeletion, [&]()
                { particle_deletion.exec(); });
        }

        return;
    }
    throw std::runtime_error(
        "FluidSimulationBuilder::buildBoundaryConditionsIfPresent: unsupported: " + type);
}
//=================================================================================================//
template <class MethodContainerType>
fluid_dynamics::AbstractBidirectionalBoundary &FluidSimulationBuilder::createBiDirectionBoundary(
    AlignedBoxByCell &aligned_box_by_cell, EntityManager &config_manager,
    MethodContainerType &main_methods, const json &config)
{
    if (config.contains("pressure"))
    {
        auto &bi_directional_bd = main_methods.template addGeneralDynamics<
            fluid_dynamics::BidirectionalBoundaryCK, LinearCorrectionCK, PressurePrescribed<>>(
            aligned_box_by_cell, config.at("pressure").get<Real>());
        return bi_directional_bd;
    }
    throw std::runtime_error(
        "FluidSimulationBuilder::createBiDirectionBoundary: unsupported boundary condition type");
}
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType, class ContactRelationType>
void FluidSimulationBuilder::buildSurfaceIndicationIfOpenBoundary(
    SPHSimulation &sim, MethodContainerType &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        auto &fluid_surface_indication =
            main_methods.template addInteractionDynamicsWithUpdate<
                            fluid_dynamics::FreeSurfaceIndicationCK>(inner_relation)
                .addPostContactInteraction(contact_relation);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialParticleIndicationTagging, [&]()
            { fluid_surface_indication.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleIndicationTagging, [&]()
            { fluid_surface_indication.exec(); });
    }
}
//=================================================================================================//
template <class MethodContainerType>
void FluidSimulationBuilder::buildParticleSortIfPresent(
    SPHSimulation &sim, MethodContainerType &main_methods, RealBody &real_body)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();

    if (fluid_solver_config.particle_sorting_)
    {
        auto &particle_sort = main_methods.addSortDynamics(real_body);

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleSort, [&]()
            {
                if (time_stepper.getIterationStep() % fluid_solver_config.sort_frequency_ == 0)
                {
                    particle_sort.exec();
                } });
    }
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_SIMULATION_BUILDER_HPP
