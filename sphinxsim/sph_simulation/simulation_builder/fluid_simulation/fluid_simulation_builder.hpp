#ifndef FLUID_SIMULATION_BUILDER_HPP
#define FLUID_SIMULATION_BUILDER_HPP

#include "fluid_simulation_builder.h"

#include <cmath>

#include "geometry_builder.h"
#include "sph_simulation.h"
#include "thermal_dynamics_builder.hpp"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
BaseDynamics<void> &FluidSimulationBuilder::addDensityRegularization(
    SPHSimulation &sim, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    SPHBody &sph_body = inner_relation.getSPHBody();
    std::string body_name = sph_body.Name();

    if (sph_body.isMatterMaterial<WeaklyCompressibleFluid>())
    {
        return FluidDynamicsBuilder::buildDensityRegularization<WeaklyCompressibleFluid>(
            sim, main_methods, inner_relation, contact_relation, fluid_solver_config.surface_type_);
    }

    if (sph_body.isMatterMaterial<WeaklyCompressibleMixture>())
    {
        return FluidDynamicsBuilder::buildDensityRegularization<WeaklyCompressibleMixture>(
            sim, main_methods, inner_relation, contact_relation, fluid_solver_config.surface_type_);
    }

    throw std::runtime_error(
        "FluidSimulationBuilder::addDensityRegularization: no supported fluid type found!");
}
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
BaseDynamics<void> &FluidSimulationBuilder::addLinearCorrectionMatrixWithScope(
    EntityManager &config_manager, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &fluid_linear_correction_matrix = main_methods.addParticleDynamicsGroup();
    fluid_linear_correction_matrix.add(
        &main_methods.template addInteractionDynamicsWithUpdate<LinearCorrectionMatrix>(inner_relation, 0.5)
             .addPostContactInteraction(contact_relation));

    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        fluid_linear_correction_matrix.add(
            &main_methods.template addStateDynamics<LinearCorrectionMatrixScope, BulkParticles>(
                inner_relation.getSPHBody()));
    }

    return fluid_linear_correction_matrix;
}
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
void FluidSimulationBuilder::buildTransportVelocityFormulationIfNotFreeSurface(
    SPHSimulation &sim, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    EntityManager &config_manager = sim.getConfigManager();

    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.surface_type_ != "free_surface")
    {
        auto &kernel_gradient_integral =
            main_methods.template addInteractionDynamics<
                            KernelGradientIntegral, LinearCorrectionCK>(inner_relation)
                .template addPostContactInteraction<Boundary, LinearCorrectionCK>(contact_relation);

        BaseDynamics<void> &transport_velocity_correction =
            addTransportVelocityCorrection(main_methods, inner_relation.getSPHBody(), fluid_solver_config);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [&]()
            {   kernel_gradient_integral.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialAfterKernelGradientIntegral);
            transport_velocity_correction.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            {   kernel_gradient_integral.exec();
            simulation_pipeline.run_hooks(SimulationHookPoint::AfterKernelGradientIntegral);
            transport_velocity_correction.exec(); });
    }
}
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
void FluidSimulationBuilder::buildViscousForceIfPresent(
    SPHSimulation &sim, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    EntityManager &config_manager = sim.getConfigManager();
    SPHBody &sph_body = inner_relation.getSPHBody();
    if (config_manager.hasEntity<Viscosity>(sph_body.Name() + "Viscosity"))
    {
        auto &viscous_force =
            main_methods.template addInteractionDynamicsWithUpdate<
                            ViscousForceCK, Viscosity, NoKernelCorrectionCK>(inner_relation)
                .template addPostContactInteraction<Wall, Viscosity, NoKernelCorrectionCK>(contact_relation);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [&]()
            { viscous_force.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { viscous_force.exec(); });
    }
}
//=================================================================================================//
template <class InnerRelationType, class ContactRelationType>
void FluidSimulationBuilder::buildSurfaceIndicationIfOpenBoundary(
    SPHSimulation &sim, MainMethods &main_methods,
    InnerRelationType &inner_relation, ContactRelationType &contact_relation)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_solver_config.surface_type_ == "open_boundary" || fluid_solver_config.surface_type_ == "free_stream")
    {
        auto &fluid_surface_indication =
            main_methods.template addInteractionDynamicsWithUpdate<
                            FreeSurfaceIndicationCK>(inner_relation)
                .addPostContactInteraction(contact_relation);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::AfterInitialCondition, [&]()
            { fluid_surface_indication.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterUpdateConfiguration, [&]()
            { fluid_surface_indication.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_SIMULATION_BUILDER_HPP
