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
    EntityManager &config_manager, MethodContainerType &method_container,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &fluid_density_regularization =
        method_container.template addInteractionDynamics<fluid_dynamics::DensitySummationCK>(inner_relation)
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
BaseDynamics<void> &FluidSimulationBuilder::addTransportVelocityCorrection(
    EntityManager &config_manager, MethodContainerType &method_container,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &transport_velocity_correction =
        method_container.template addInteractionDynamics<KernelGradientIntegral, LinearCorrectionCK>(inner_relation)
            .template addPostContactInteraction<Boundary, NoKernelCorrectionCK>(contact_relation);

    auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_solver_config.surface_type_ == "confined")
    {
        transport_velocity_correction.template addPostStateDynamics<
            fluid_dynamics::TransportVelocityCorrectionCK, TruncatedLinear>(inner_relation.getSPHBody());
        return transport_velocity_correction;
    }

    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        transport_velocity_correction.template addPostStateDynamics<
            fluid_dynamics::TransportVelocityCorrectionCK, TruncatedLinear, BulkParticles>(
            inner_relation.getSPHBody());
        return transport_velocity_correction;
    }
    throw std::runtime_error(
        "FluidSimulationBuilder::addTransportVelocityCorrection: no supported flow type found!");
}
//=================================================================================================//
template <class MethodContainerType>
void FluidSimulationBuilder::addBoundaryConditions(
    SPHSimulation &sim, MethodContainerType &method_container, const json &config)
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
        auto &inflow_condition = method_container.template addStateDynamics<
            fluid_dynamics::EmitterInflowConditionCK, ConstantInflowSpeed>(
            emitter, config.at("inflow_speed").get<Real>());
        auto &injection = method_container.template addStateDynamics<
            fluid_dynamics::EmitterInflowInjectionCK>(emitter);

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryConditions, [&]()
            { inflow_condition.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { injection.exec(); });

        return;
    }

    if (type == "bi_directional")
    {
        auto &aligned_box_by_cell = fluid_body.addBodyPart<AlignedBoxByCell>(aligned_box);
        auto &bi_directional_bd = addBiDirectionBoundary(
            aligned_box_by_cell, config_manager, method_container, config);

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialParticleIndicationTagging, [&]()
            { bi_directional_bd.tagBufferParticles(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryConditions, [&]()
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
            auto &particle_deletion = method_container.template addStateDynamics<
                fluid_dynamics::OutflowParticleDeletion>(fluid_body);
            fluid_solver_config.particle_deletion_ = true; // enable particle deletion

            simulation_pipeline.insert_hook(
                SimulationHookPoint::ParticleDeletion, [&]()
                { particle_deletion.exec(); });
        }
        
        return;
    }
    throw std::runtime_error(
        "FluidSimulationBuilder::addBoundaryConditions: unsupported: " + type);
}
//=================================================================================================//
template <class MethodContainerType>
fluid_dynamics::AbstractBidirectionalBoundary &FluidSimulationBuilder::addBiDirectionBoundary(
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
        "FluidSimulationBuilder::addBiDirectionBoundary: unsupported boundary condition type");
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_SIMULATION_BUILDER_HPP
