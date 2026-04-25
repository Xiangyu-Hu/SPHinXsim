#include "fluid_simulation_builder.hpp"

#include "base_simulation_builder.hpp"

namespace SPH
{
//=================================================================================================//
void FluidSimulationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    // SPHSystem and entity manager.
    // Basically, the SPHSystem is the container of all SPH simulation objects,
    // and the entity manager is the container of all simulation setting
    // configurations and external (not SPH) simulation environments.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = sim.defineSPHSystem();
    EntityManager &config_manager = sim.getConfigManager();
    //----------------------------------------------------------------------
    // Creating bodies with inital geometry, materials and particles.
    //----------------------------------------------------------------------
    buildFluidBodies(sph_system, config_manager, config.at("fluid_bodies"));
    buildSolidBodies(sph_system, config_manager, config.at("solid_bodies"));
    //----------------------------------------------------------------------
    // Define body relation map.
    // The relations give the topological connections within (inner) a body
    // or with (contact) other bodies within interaction range.
    // Generally, we first define all the inner relations,
    // then the contact relations.
    //----------------------------------------------------------------------
    auto &fluid_body = *sph_system.collectBodies<FluidBody>().front(); // assume only one fluid body for now
    StdVec<SolidBody *> solid_bodies = sph_system.collectBodies<SolidBody>();
    auto &fluid_inner = sph_system.addInnerRelation(fluid_body);
    auto &fluid_wall_contact = sph_system.addContactRelation(fluid_body, solid_bodies);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = sim.defineSPHSolver(*this, config);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    //----------------------------------------------------------------------
    // Define the main numerical methods used in the simulation.
    // Note that there may be data dependence on the sequence of constructions.
    // Generally, the configuration dynamics, such as update cell linked list,
    // update body relations, are defined first.
    //----------------------------------------------------------------------
    auto &solid_cell_linked_list = main_methods.addCellLinkedListDynamics(solid_bodies);
    auto &fluid_configuration =
        main_methods.addParticleDynamicsGroup()
            .add(&main_methods.addCellLinkedListDynamics(fluid_body))
            .add(&main_methods.addRelationDynamics(fluid_inner, fluid_wall_contact));

    auto &fluid_advection_step_setup = main_methods.addStateDynamics<fluid_dynamics::AdvectionStepSetup>(fluid_body);
    auto &fluid_particle_position = main_methods.addStateDynamics<fluid_dynamics::UpdateParticlePosition>(fluid_body);

    auto &fluid_linear_correction_matrix =
        main_methods.addInteractionDynamics<LinearCorrectionMatrix, WithUpdate>(fluid_inner, 0.5)
            .addPostContactInteraction(fluid_wall_contact);

    auto &fluid_acoustic_step_1st_half =
        main_methods.addInteractionDynamicsOneLevel<
                        fluid_dynamics::AcousticStep1stHalf, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_inner)
            .addPostContactInteraction<Wall, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_wall_contact);

    auto &fluid_acoustic_step_2nd_half =
        main_methods.addInteractionDynamicsOneLevel<
                        fluid_dynamics::AcousticStep2ndHalf, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_inner)
            .addPostContactInteraction<Wall, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_wall_contact);

    auto &fluid_density_regularization = addDensitySummationAndRegularization(
        config_manager, main_methods, fluid_inner, fluid_wall_contact);

    auto &fluid_solver_config = config_manager.getEntityByName<FluidSolverConfig>("FluidSolverConfig");
    Fluid &fluid_material = DynamicCast<Fluid>(this, fluid_body.getBaseMaterial());
    const Real U_ref = fluid_material.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    auto &fluid_advection_time_step = main_methods.addReduceDynamics<fluid_dynamics::AdvectionTimeStepCK>(
        fluid_body, U_ref, fluid_solver_config.advection_cfl_);
    auto &fluid_acoustic_time_step = main_methods.addReduceDynamics<fluid_dynamics::AcousticTimeStepCK<>>(
        fluid_body, fluid_solver_config.acoustic_cfl_);
    //----------------------------------------------------------------------
    // Define the methods for I/O operations, observations
    // and monitoring of reduced information
    //----------------------------------------------------------------------
    auto &body_state_recorder = createBodyStatesRecording(sph_system, config_manager, main_methods, config);
    //----------------------------------------------------------------------
    //	Define time integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &solver_common_config = config_manager.getEntityByName<SolverCommonConfig>("SolverCommonConfig");
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(fluid_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(solver_common_config.output_interval_);
    time_stepper.setScreeningInterval(solver_common_config.screen_interval_);
    //----------------------------------------------------------------------
    //	Define preparation or initialization step before the main integration.
    //----------------------------------------------------------------------
    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialCondition);

            solid_cell_linked_list.exec();
            fluid_configuration.exec();

            fluid_linear_correction_matrix.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialParticleIndicationTagging);
            fluid_density_regularization.exec();
            fluid_advection_step_setup.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialAfterAdvectionStepSetup);

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialObservation);
            body_state_recorder.writeToFile();
        });
    //----------------------------------------------------------------------
    // Define the time integration method.
    // Here we use dual time stepping with acoustic and advection steps.
    // The acoustic step is executed every physical time step, while the advection step is
    // executed at a lower frequency determined by the advection time step.
    // Note that only in acoustic steps the time integration is carried out.
    //----------------------------------------------------------------------
    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.main_steps.push_back( // acoustic or integration step
        [&]()
        {
            Real dt = time_stepper.incrementPhysicalTime(fluid_acoustic_time_step);
            fluid_acoustic_step_1st_half.exec(dt);
            simulation_pipeline.run_hooks(SimulationHookPoint::BoundaryCondition);
            fluid_acoustic_step_2nd_half.exec(dt);
        });

    simulation_pipeline.main_steps.push_back( // advection or particle configuration step
        [&]()
        {
            if (advection_step(fluid_advection_time_step))
            {
                fluid_particle_position.exec();
                time_stepper.incrementIterationStep();

                if (time_stepper.isFirstComputingStep() || time_stepper.isScreeningStep())
                {
                    std::cout << std::fixed << std::setprecision(9)
                              << "N=" << time_stepper.getIterationStep()
                              << "  Time = " << time_stepper.getPhysicalTime()
                              << "  advection_dt = " << advection_step.getInterval()
                              << "  acoustic_dt = " << time_stepper.getGlobalTimeStepSize()
                              << "\n";
                }

                if (time_stepper.isObservationStep())
                {
                    simulation_pipeline.run_hooks(SimulationHookPoint::Observation);
                }

                if (state_recording_trigger())
                {
                    body_state_recorder.writeToFile();
                }

                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleCreation);
                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleDeletionTagging);
                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleDeletion);
                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleSort);

                fluid_configuration.exec();
                fluid_linear_correction_matrix.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleIndicationTagging);
                fluid_density_regularization.exec();
                fluid_advection_step_setup.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::AfterAdvectionStepSetup);
            }
        });
    //----------------------------------------------------------------------
    // Define optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    buildObservationIfPresent(sim, main_methods, config);
    buildExternalForceIfPresent(sim, main_methods, fluid_body, config);
    buildSurfaceIndicationIfOpenBoundary(sim, main_methods, fluid_inner, fluid_wall_contact);
    buildTransportVelocityFormulationIfNotFreeSurface(sim, main_methods, fluid_inner, fluid_wall_contact);
    buildViscousForceIfPresent(sim, main_methods, fluid_inner, fluid_wall_contact);
    buildBoundaryConditionsIfPresent(sim, main_methods, config);
    buildParticleSortIfPresent(sim, main_methods, fluid_body);
}
//=================================================================================================//
void FluidSimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    SimulationBuilder::parseSolverParameters(config_manager, config);
    if (config.contains("fluid_dynamics"))
    {
        config_manager.emplaceEntity<FluidSolverConfig>(
            "FluidSolverConfig", parseFluidSolverConfig(config.at("fluid_dynamics")));
    }
}
//=================================================================================================//
FluidSolverConfig FluidSimulationBuilder::parseFluidSolverConfig(const json &config)
{
    FluidSolverConfig params;
    if (config.contains("acoustic_cfl"))
        params.acoustic_cfl_ = config.at("acoustic_cfl").get<Real>();
    if (config.contains("advection_cfl"))
        params.advection_cfl_ = config.at("advection_cfl").get<Real>();
    if (config.contains("flow_type"))
        params.surface_type_ = config.at("flow_type").get<std::string>();
    if (config.contains("particle_sort_frequency"))
    {
        params.particle_sorting_ = true;
        params.sort_frequency_ = config.at("particle_sort_frequency").get<UnsignedInt>();
    }
    return params;
}
//=================================================================================================//
} // namespace SPH
