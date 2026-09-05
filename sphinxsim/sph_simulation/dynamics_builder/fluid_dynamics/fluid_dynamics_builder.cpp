#include "fluid_dynamics_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addAdvectionStepSetup(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &advection_step_setup = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        advection_step_setup.add(&main_methods.addStateDynamics<AdvectionStepSetup>(
            fluid_body));
    }
    return advection_step_setup;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addUpdateParticlePosition(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &update_particle_position = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        update_particle_position.add(
            &main_methods.addStateDynamics<UpdateParticlePosition>(fluid_body));
    }
    return update_particle_position;
}
//=================================================================================================//
BaseDynamics<Real> &FluidDynamicsBuilder::addAdvectionTimeStep(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &advection_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &viscosity_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &fluid_solver_parameters = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        advection_time_step.add(&main_methods.addReduceDynamics<AdvectionTimeStepCK>(
            fluid_body, Real(1), fluid_solver_parameters.advection_cfl_));

        if (config_manager.hasEntity<Viscosity>(body_name + "Viscosity"))
        {
            viscosity_time_step.add(&main_methods.addReduceDynamics<AdvectionViscousTimeStepCK>(
                fluid_body, Real(1), fluid_solver_parameters.advection_cfl_));
        }
    }

    if (viscosity_time_step.hasDynamics())
    {
        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::PreSimulationSanityCheck, [&]()
            { 
            auto advection_time_step_size = advection_time_step.exec();
            auto viscosity_time_step_size = viscosity_time_step.exec();
            if ( advection_time_step_size  - viscosity_time_step_size > Eps )
            {
                std::cout << "\n------------------------------------------------------------" << std::endl;
                std::cout << "Error: Advection time step is too large for viscous flow!" << std::endl;
                std::cout << "Advection time step: " << advection_time_step_size << std::endl;
                std::cout << "Viscous time step: " << viscosity_time_step_size << std::endl;
                std::cout << "The particle spacing is unnecessarily small for viscous flow." << std::endl;
                std::cout << "------------------------------------------------------------" << std::endl;
                exit(1);
            } });
    }

    return advection_time_step;
}
//=================================================================================================//
BaseDynamics<Real> &FluidDynamicsBuilder::addAcousticTimeStep(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        acoustic_time_step.add(&addAcousticTimeStepForOneBody(sim, fluid_body, main_methods));
    }
    return acoustic_time_step;
}
//=================================================================================================//
BaseDynamics<Real> &FluidDynamicsBuilder::addAcousticTimeStepForOneBody(
    SPHSimulation &sim, FluidBody &fluid_body, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_body.isMatterMaterial<WeaklyCompressibleFluid>())
    {
        return main_methods.addReduceDynamics<
            AcousticTimeStepCK<WeaklyCompressibleFluid>>(
            fluid_body, fluid_solver_config.acoustic_cfl_);
    }

    if (fluid_body.isMatterMaterial<WeaklyCompressibleMixture>())
    {
        return main_methods.addReduceDynamics<
            AcousticTimeStepCK<WeaklyCompressibleMixture>>(
            fluid_body, fluid_solver_config.acoustic_cfl_);
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::addAcousticTimeStepForOneBody: no supported material type found!");
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticStep1stHalf(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_step_1st_half = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        acoustic_step_1st_half.add(&addAcousticHalfStepForOneBody<AcousticStep1stHalf>(
            sim, inner_relation, main_methods));
    }
    return acoustic_step_1st_half;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticStep2ndHalf(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_step_2nd_half = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        acoustic_step_2nd_half.add(&addAcousticHalfStepForOneBody<AcousticStep2ndHalf>(
            sim, inner_relation, main_methods));
    }
    return acoustic_step_2nd_half;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addLinearCorrectionMatrix(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    auto &fluid_linear_correction_matrix = main_methods.addParticleDynamicsGroup();
    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &contact_relation = sph_system.getRelationByName<Contact<Relation<FluidBody, SolidBody>>>(
            body_name + solid_bodies_config.front()->name_);
        fluid_linear_correction_matrix.add(
            &main_methods.addInteractionDynamicsWithUpdate<LinearCorrectionMatrix>(inner_relation, 0.5)
                 .addPostContactInteraction(contact_relation));
        if (fluid_solver_config.surface_type_ == "open_boundary")
        {
            fluid_linear_correction_matrix.add(
                &main_methods.addStateDynamics<LinearCorrectionMatrixScope, BulkParticles>(
                    inner_relation.getSPHBody()));
        }
    }
    return fluid_linear_correction_matrix;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addDensityRegularization(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    auto &fb = fluid_bodies_config.front();
    std::string body_name = fb->name_;
    auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
    auto &contact_relation = sph_system.getRelationByName<Contact<Relation<FluidBody, SolidBody>>>(
        body_name + solid_bodies_config.front()->name_);
    SPHBody &sph_body = inner_relation.getSPHBody();

    if (sph_body.isMatterMaterial<WeaklyCompressibleFluid>())
    {
        return buildDensityRegularization<WeaklyCompressibleFluid>(
            sim, main_methods, inner_relation, contact_relation, fluid_solver_config.surface_type_);
    }
    if (sph_body.isMatterMaterial<WeaklyCompressibleMixture>())
    {
        return buildDensityRegularization<WeaklyCompressibleMixture>(
            sim, main_methods, inner_relation, contact_relation, fluid_solver_config.surface_type_);
    }
    throw std::runtime_error(
        "FluidDynamicsBuilder::addDensityRegularization: no supported fluid type found!");
}
//=================================================================================================//
void FluidDynamicsBuilder::buildViscousForceIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &contact_relation = sph_system.getRelationByName<Contact<Relation<FluidBody, SolidBody>>>(
            body_name + solid_bodies_config.front()->name_);
        SPHBody &sph_body = inner_relation.getSPHBody();
        if (config_manager.hasEntity<Viscosity>(sph_body.Name() + "Viscosity"))
        {
            auto &viscous_force =
                main_methods.addInteractionDynamicsWithUpdate<
                                ViscousForceCK, Viscosity, NoKernelCorrectionCK>(inner_relation)
                    .addPostContactInteraction<Wall, Viscosity, NoKernelCorrectionCK>(contact_relation);
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
}
//=================================================================================================//
void FluidDynamicsBuilder::buildSurfaceIndicationIfOpenBoundary(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.surface_type_ != "open_boundary" &&
        fluid_solver_config.surface_type_ != "free_stream")
    {
        return;
    }
    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &contact_relation = sph_system.getRelationByName<Contact<Relation<FluidBody, SolidBody>>>(
            body_name + solid_bodies_config.front()->name_);
        auto &fluid_surface_indication =
            main_methods.addInteractionDynamicsWithUpdate<FreeSurfaceIndicationCK>(inner_relation)
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
BaseDynamics<void> &FluidDynamicsBuilder::addTransportVelocityCorrection(
    MainMethods &main_methods, SPHBody &sph_body, FluidSolverConfig &fluid_solver_config)
{
    if (fluid_solver_config.surface_type_ == "confined")
    {
        return main_methods.addStateDynamics<TransportVelocityCorrectionCK, TruncatedLinear>(sph_body);
    }
    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        return main_methods.addStateDynamics<TransportVelocityCorrectionCK, TruncatedLinear, BulkParticles>(sph_body);
    }
    if (fluid_solver_config.surface_type_ == "free_stream")
    {
        return main_methods.addStateDynamics<TransportVelocityCorrectionCK, NoLimiter, BulkParticles>(sph_body);
    }
    throw std::runtime_error(
        "FluidDynamicsBuilder::addTransportVelocityCorrection: no supported flow type found!");
}
//=================================================================================================//
void FluidDynamicsBuilder::buildTransportVelocityFormulationIfNotFreeSurface(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.surface_type_ == "free_surface")
    {
        return;
    }
    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &contact_relation = sph_system.getRelationByName<Contact<Relation<FluidBody, SolidBody>>>(
            body_name + solid_bodies_config.front()->name_);
        auto &kernel_gradient_integral =
            main_methods.addInteractionDynamics<KernelGradientIntegral, LinearCorrectionCK>(inner_relation)
                .addPostContactInteraction<Boundary, LinearCorrectionCK>(contact_relation);
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
} // namespace SPH
