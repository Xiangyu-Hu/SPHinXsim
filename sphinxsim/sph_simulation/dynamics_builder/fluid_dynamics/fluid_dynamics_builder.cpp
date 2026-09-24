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

    auto &all_linear_correction_matrix = main_methods.addParticleDynamicsGroup();
    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &linear_correction_matrix = main_methods.template addInteractionDynamicsWithUpdate<
            LinearCorrectionMatrix>(inner_relation, 0.5);
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        addInteractionWithSolidBodies(sim, linear_correction_matrix, fluid_body);
        all_linear_correction_matrix.add(&linear_correction_matrix);

        auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
        if (fluid_solver_config.surface_type_ == "open_boundary")
        {
            all_linear_correction_matrix.add(
                &main_methods.addStateDynamics<LinearCorrectionMatrixScope, BulkParticles>(fluid_body));
        }
    }
    return all_linear_correction_matrix;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addDensityRegularization(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    auto &density_regularization = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &density_summation = main_methods.template addInteractionDynamics<
            CompressionSummation>(inner_relation);
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        addInteractionWithSolidBodies(sim, density_summation, fluid_body);
        density_regularization.add(&density_summation);

        auto &average_compression = main_methods.template addReduceDynamics<AverageCompression>(fluid_body);
        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { 
            density_summation.exec();
            Real average_compression_value = average_compression.exec();
            std::cout << "\n------------------------------------------------------------" << std::endl;
            std::cout << "FluidDynamicsBuilder::addDensityRegularization : " 
                      << "Initial average compression of FluidBody '" << fluid_body.Name() 
                      << "' is " << average_compression_value << std::endl; 
            std::cout << "------------------------------------------------------------" << std::endl; });

        if (fluid_body.isMatterMaterial<WeaklyCompressibleFluid>())
        {
            density_regularization.add(&addDensityRegularizationForOneBody<WeaklyCompressibleFluid>(
                main_methods, fluid_body, fluid_solver_config.surface_type_));
        }
        else
        {
            density_regularization.add(&addDensityRegularizationForOneBody<WeaklyCompressibleMixture>(
                main_methods, fluid_body, fluid_solver_config.surface_type_));
        }

        auto &minimum_compression =
            main_methods.template addReduceDynamics<
                QuantityReduce, IndexedMin, SimpleEvaluation<IndexedValue<Real>>>(
                fluid_body, "Compression");
        auto &maximum_compression =
            main_methods.template addReduceDynamics<
                QuantityReduce, IndexedMax, SimpleEvaluation<IndexedValue<Real>>>(
                fluid_body, "Compression");

        initialization_pipeline.insert_hook(
            InitializationHookPoint::PreSimulationSanityCheck, [&]()
            {
            // This bound is calibrated for freshly relaxed particles. A developed free-stream
            // flow legitimately carries values outside it near the open boundary, so a restored
            // state would fail a check the continuous run at the same step would also fail. See issue #170.
            if (config_manager.hasEntity<RestartConfig>("RestartConfig") &&
                config_manager.getEntity<RestartConfig>("RestartConfig").restore_step_ > 0 &&
                config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig").surface_type_ == "free_stream")
                return; 
            auto lower_limit = minimum_compression.exec();
            auto upper_limit = maximum_compression.exec();
            if (lower_limit.first < 0.95 || upper_limit.first > 1.05 ||
                std::isnan(lower_limit.first) || std::isnan(upper_limit.first))
            {
                std::cout << "\n------------------------------------------------------------" << std::endl;
                std::cout << "Error: Compression is out of range!" << std::endl;
                std::cout << "Lower limit: " << lower_limit.first << " at particle " << lower_limit.second << std::endl;
                std::cout << "Upper limit: " << upper_limit.first << " at particle " << upper_limit.second << std::endl;
                std::cout << "The possible issues are the following:" << std::endl;
                std::cout << "- Too large: overlapped bodies" << std::endl;
                std::cout << "- Too small: insufficient resolution due to thin layer" << std::endl;
                std::cout << "------------------------------------------------------------" << std::endl;
                exit(1);
            } });
    }

    return density_regularization;
}
//=================================================================================================//
void FluidDynamicsBuilder::buildViscousForceIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &all_viscous_force = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        if (config_manager.hasEntity<Viscosity>(body_name + "Viscosity"))
        {
            auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
            auto &viscous_force = main_methods.addInteractionDynamicsWithUpdate<
                ViscousForceCK, Viscosity, NoKernelCorrectionCK>(inner_relation);
            auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
            addInteractionWithSolidBodies<Wall, Viscosity, NoKernelCorrectionCK>(
                sim, viscous_force, fluid_body);
            all_viscous_force.add(&viscous_force);
        }
    }

    if (all_viscous_force.hasDynamics())
    {
        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [&]()
            { all_viscous_force.exec(); });
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { all_viscous_force.exec(); });
    }
}
//=================================================================================================//
void FluidDynamicsBuilder::buildSurfaceIndicationIfOpenBoundary(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &all_surface_indication = main_methods.addParticleDynamicsGroup();

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
        auto &fluid_surface_indication = main_methods.addInteractionDynamicsWithUpdate<
            FreeSurfaceIndicationCK>(inner_relation);
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        addInteractionWithSolidBodies(sim, fluid_surface_indication, fluid_body);
        all_surface_indication.add(&fluid_surface_indication);
    }

    if (all_surface_indication.hasDynamics())
    {
        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::AfterInitialCondition, [&]()
            { all_surface_indication.exec(); });
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterUpdateConfiguration, [&]()
            { all_surface_indication.exec(); });
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
    auto &all_kernel_gradient_integral = main_methods.addParticleDynamicsGroup();
    auto &all_transport_velocity_correction = main_methods.addParticleDynamicsGroup();

    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.surface_type_ == "free_surface")
    {
        return;
    }
    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        auto &kernel_gradient_integral = main_methods.addInteractionDynamics<
            KernelGradientIntegral, LinearCorrectionCK>(inner_relation);
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        addInteractionWithSolidBodies<Boundary, LinearCorrectionCK>(
            sim, kernel_gradient_integral, fluid_body);
        all_kernel_gradient_integral.add(&kernel_gradient_integral);

        all_transport_velocity_correction.add(
            &addTransportVelocityCorrection(main_methods, fluid_body, fluid_solver_config));
    }

    if (all_kernel_gradient_integral.hasDynamics())
    {
        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [&]()
            {   all_kernel_gradient_integral.exec();
                initialization_pipeline.run_hooks(InitializationHookPoint::InitialAfterKernelGradientIntegral);
                all_transport_velocity_correction.exec(); });
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            {   all_kernel_gradient_integral.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::AfterKernelGradientIntegral);
                all_transport_velocity_correction.exec(); });
    }
}
//=================================================================================================//
void FluidDynamicsBuilder::buildParticleDeletionIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (!fluid_solver_config.particle_deletion_)
        return;

    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &sph_system = sim.getSPHSystem();
    auto &all_particle_deletion = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        all_particle_deletion.add(&main_methods.template addStateDynamics<
                                   OutflowParticleDeletion>(fluid_body));
    }

    if (all_particle_deletion.hasDynamics())
    {
        StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleDeletion, [&]()
            { all_particle_deletion.exec(); });
    }
}
//=================================================================================================//
void FluidDynamicsBuilder::buildParticleSortIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{

    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (!fluid_solver_config.particle_sorting_)
        return;

    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &sph_system = sim.getSPHSystem();
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();
    auto &all_particle_sort = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        all_particle_sort.add(&main_methods.addSortDynamics(fluid_body));
    }

    if (all_particle_sort.hasDynamics())
    {
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleSort, [&]()
            {
                if (time_stepper.getIterationStep() % fluid_solver_config.sort_frequency_ == 0)
                {
                    all_particle_sort.exec();
                } });
    }
}

//=================================================================================================//
} // namespace SPH
