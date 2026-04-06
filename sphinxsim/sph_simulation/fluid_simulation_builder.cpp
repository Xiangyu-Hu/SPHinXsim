#include "fluid_simulation_builder.hpp"

namespace SPH
{
//=================================================================================================//
void FluidSimulationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = sim.defineSPHSystem(config);
    EntityManager &entity_manager = sim.getEntityManager();
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
    for (const auto &fb : config.at("fluid_bodies"))
        sim.addFluidBody(sph_system, entity_manager, fb);
    for (const auto &sb : config.at("solid_bodies"))
        sim.addSolidBody(sph_system, entity_manager, sb);
    if (config.contains("observers"))
        for (const auto &obs : config.at("observers"))
            sim.addObserver(sph_system, entity_manager, obs);
    //----------------------------------------------------------------------
    //	Define body relation map.
    //	The relations give the topological connections within a body
    //  or with other bodies within interaction range.
    //  Generally, we first define all the inner relations, then the contact relations.
    //----------------------------------------------------------------------
    auto &fluid_body = *entity_manager.entitiesWith<FluidBody>().front(); // assume only one fluid body for now
    StdVec<SolidBody *> solid_bodies = entity_manager.entitiesWith<SolidBody>();
    auto &fluid_observer = *entity_manager.entitiesWith<ObserverBody>().front(); // assume only one observer body for now

    auto &fluid_inner = sph_system.addInnerRelation(fluid_body);
    auto &fluid_wall_contact = sph_system.addContactRelation(fluid_body, solid_bodies);
    auto &fluid_observer_contact = sph_system.addContactRelation(fluid_observer, fluid_body);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = sim.defineSPHSolver(sph_system, config);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
    //----------------------------------------------------------------------
    // Solver control parameters.
    //----------------------------------------------------------------------
    if (config.contains("fluid_solver_parameters"))
    {
        updateSolverParameters(sim, config.at("fluid_solver_parameters"));
    }
    //----------------------------------------------------------------------
    // Define the numerical methods used in the simulation.
    // Note that there may be data dependence on the sequence of constructions.
    // Generally, the configuration dynamics, such as update cell linked list,
    // update body relations, are defined first.
    // Then the geometric models or simple objects without data dependencies,
    // such as gravity, initialized normal direction.
    // After that, the major physical particle dynamics model should be introduced.
    // Finally, the auxiliary models such as time step estimator, initial condition,
    // boundary condition and other constraints should be defined.
    //----------------------------------------------------------------------
    auto &solid_normal_direction = host_methods.addStateDynamics<NormalFromBodyShapeCK>(solid_bodies);

    auto &solid_cell_linked_list = main_methods.addCellLinkedListDynamics(solid_bodies);
    auto &fluid_update_configuration = main_methods.addParticleDynamicsGroup()
                                           .add(&main_methods.addCellLinkedListDynamics(fluid_body))
                                           .add(&main_methods.addRelationDynamics(fluid_inner, fluid_wall_contact));
    auto &observer_update_configuration = main_methods.addRelationDynamics(fluid_observer_contact);

    auto &fluid_advection_step_setup = main_methods.addStateDynamics<fluid_dynamics::AdvectionStepSetup>(fluid_body);
    auto &fluid_update_particle_position = main_methods.addStateDynamics<fluid_dynamics::UpdateParticlePosition>(fluid_body);

    auto &fluid_linear_correction_matrix =
        main_methods.addInteractionDynamics<LinearCorrectionMatrix, WithUpdate>(fluid_inner, 0.5)
            .addPostContactInteraction(fluid_wall_contact);

    auto &fluid_acoustic_step_1st_half =
        main_methods.addInteractionDynamics<fluid_dynamics::AcousticStep1stHalf, OneLevel,
                                            AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_inner)
            .addPostContactInteraction<Wall, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_wall_contact);

    auto &fluid_acoustic_step_2nd_half =
        main_methods.addInteractionDynamics<fluid_dynamics::AcousticStep2ndHalf, OneLevel,
                                            AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_inner)
            .addPostContactInteraction<Wall, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_wall_contact);

    auto &fluid_density_regularization =
        main_methods.addInteractionDynamics<fluid_dynamics::DensitySummationCK>(fluid_inner)
            .addPostContactInteraction(fluid_wall_contact)
            .addPostStateDynamics<fluid_dynamics::DensityRegularization, FreeSurface>(fluid_body);

    Fluid &fluid_material = DynamicCast<Fluid>(this, fluid_body.getBaseMaterial());
    const Real U_ref = fluid_material.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    auto &fluid_advection_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AdvectionTimeStepCK>(fluid_body, U_ref, solver_parameters_.advection_cfl);
    auto &fluid_acoustic_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AcousticTimeStepCK<>>(fluid_body, solver_parameters_.acoustic_cfl);
    //----------------------------------------------------------------------
    //	Define the methods for I/O operations, observations
    //	and regression tests of the simulation.
    //----------------------------------------------------------------------
    auto &body_state_recorder = main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);
    for (auto &solid_body : solid_bodies)
    {
        body_state_recorder.addToWrite<Vecd>(*solid_body, "NormalDirection");
    }
    body_state_recorder.addToWrite<Real>(fluid_body, "Density");
    auto &observer_pressure_output = main_methods.addObserveRecorder<Real>("Pressure", fluid_observer_contact);
    //----------------------------------------------------------------------
    //	Define Preparation or initialization step for the time integration loop.
    //----------------------------------------------------------------------
    StagePipeline<InitializationHookPoint> &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            solid_normal_direction.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialConditions);

            solid_cell_linked_list.exec();
            fluid_update_configuration.exec();

            fluid_density_regularization.exec();
            fluid_advection_step_setup.exec();
            fluid_linear_correction_matrix.exec();

            body_state_recorder.writeToFile();

            observer_update_configuration.exec();
            observer_pressure_output.writeToFile(advection_steps_);
        });
    //----------------------------------------------------------------------
    //	Define time-integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(fluid_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(sim.getOutputInterval());
    int screening_interval = 100;
    int observation_interval = screening_interval * 2;
    //----------------------------------------------------------------------
    //	Define time-integration method.
    //  Here we use dual time stepping with acoustic and advection steps.
    //  The acoustic step is executed every physical time step, while the advection step is
    //  executed at a lower frequency determined by the advection time step.
    //----------------------------------------------------------------------
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.main_steps.push_back( // acoustic step
        [&]()
        {
            Real dt = time_stepper.incrementPhysicalTime(fluid_acoustic_time_step);
            fluid_acoustic_step_1st_half.exec(dt);
            simulation_pipeline.run_hooks(SimulationHookPoint::BoundaryConditions);
            fluid_acoustic_step_2nd_half.exec(dt);
        });

    simulation_pipeline.main_steps.push_back( // advection step
        [&, screening_interval, observation_interval]()
        {
            if (advection_step(fluid_advection_time_step))
            {
                fluid_update_particle_position.exec();

                if (advection_steps_ % screening_interval == 0)
                {
                    std::cout << std::fixed << std::setprecision(9)
                              << "N=" << advection_steps_
                              << "  Time = " << time_stepper.getPhysicalTime()
                              << "  advection_dt = " << advection_step.getInterval()
                              << "  acoustic_dt = " << time_stepper.getGlobalTimeStepSize()
                              << "\n";
                }

                if (advection_steps_ % observation_interval == 0)
                {
                    observer_update_configuration.exec();
                    observer_pressure_output.writeToFile(advection_steps_);
                }

                if (state_recording_trigger())
                {
                    body_state_recorder.writeToFile();
                }

                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleCreation);
                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleDeletion);

                if (advection_steps_ % 100 == 0)
                {
                    simulation_pipeline.run_hooks(SimulationHookPoint::ParticleSort);
                }

                fluid_update_configuration.exec();
                fluid_density_regularization.exec();
                fluid_advection_step_setup.exec();
                fluid_linear_correction_matrix.exec();
                advection_steps_++;
            }
        });
    //----------------------------------------------------------------------
    // Define optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    if (config.contains("gravity"))
    {
        auto &constant_gravity =
            main_methods.addStateDynamics<GravityForceCK<Gravity>>(
                fluid_body, Gravity(jsonToVecd(config.at("gravity"))));
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialConditions, [&]()
            { constant_gravity.exec(); });
    }

    if (config.contains("fluid_boundary_conditions"))
    {
        for (const auto &bd : config.at("fluid_boundary_conditions"))
        {
            addBoundaryConditions(sim, main_methods, bd);
        }
    }

    if (config.contains("particle_sort_frequency")) // after all body part by particles defined
    {
        UnsignedInt frequency = config.at("particle_sort_frequency").get<UnsignedInt>();
        auto &particle_sort = main_methods.addSortDynamics(fluid_body);
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleSort, [&, frequency]()
            {
                if (advection_steps_ % frequency == 0)
                {
                    particle_sort.exec();
                } });
    }
}
//=================================================================================================//
void FluidSimulationBuilder::updateSolverParameters(SPHSimulation &sim, const json &config)
{
    if (config.contains("acoustic_cfl"))
        solver_parameters_.acoustic_cfl = config.at("acoustic_cfl").get<Real>();
    if (config.contains("advection_cfl"))
        solver_parameters_.advection_cfl = config.at("advection_cfl").get<Real>();
    if (config.contains("free_surface_correction"))
        solver_parameters_.free_surface_correction = config.at("free_surface_correction").get<bool>();
}
//=================================================================================================//
} // namespace SPH
