#include "continuum_simulation_builder.hpp"

#include "constraint_builder.hpp"

namespace SPH
{
//=================================================================================================//
void ContinuumSimulationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = sim.defineSPHSystem();
    EntityManager &entity_manager = sim.getEntityManager();
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
    for (const auto &cb : config.at("continuum_bodies"))
        sim.addContinuumBody(sph_system, entity_manager, cb);
    for (const auto &sb : config.at("solid_bodies"))
        sim.addSolidBody(sph_system, entity_manager, sb);
    //----------------------------------------------------------------------
    //	Define body relation map.
    //	The relations give the topological connections within a body
    //  or with other bodies within interaction range.
    //  Generally, we first define all the inner relations, then the contact relations.
    //----------------------------------------------------------------------
    auto &continuum_body = *entity_manager.entitiesWith<RealBody>().front(); // assume only one continuum body for now
    StdVec<SolidBody *> solid_bodies = entity_manager.entitiesWith<SolidBody>();

    auto &continuum_inner = sph_system.addInnerRelation(continuum_body);
    auto &continuum_solid_contact = sph_system.addContactRelation(continuum_body, solid_bodies);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = sim.defineSPHSolver(sph_system, config);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    //    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
    //----------------------------------------------------------------------
    // Solver control parameters.
    //----------------------------------------------------------------------
    if (config.contains("continuum_solver_parameters"))
    {
        updateSolverParameters(sim, config.at("continuum_solver_parameters"));
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
    auto &solid_cell_linked_list = main_methods.addCellLinkedListDynamics(solid_bodies);
    auto &continuum_update_configuration =
        main_methods.addParticleDynamicsGroup()
            .add(&main_methods.addCellLinkedListDynamics(continuum_body))
            .add(&main_methods.addRelationDynamics(continuum_inner, continuum_solid_contact));

    auto &continuum_advection_step_setup = main_methods.addStateDynamics<
        fluid_dynamics::AdvectionStepSetup>(continuum_body);
    auto &continuum_update_particle_position = main_methods.addStateDynamics<
        fluid_dynamics::UpdateParticlePosition>(continuum_body);

    auto &continuum_acoustic_step_1st_half = addAcousticStep1stHalf(entity_manager, main_methods, continuum_inner);
    auto &continuum_acoustic_step_2nd_half = addAcousticStep2ndHalf(entity_manager, main_methods, continuum_inner);

    Fluid &continuum_eos = DynamicCast<Fluid>(this, continuum_body.getBaseMaterial());
    const Real U_ref = continuum_eos.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    auto &continuum_advection_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AdvectionTimeStepCK>(continuum_body, U_ref, solver_parameters_.advection_cfl_);
    auto &continuum_acoustic_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AcousticTimeStepCK<>>(continuum_body, solver_parameters_.acoustic_cfl_);

    auto &continuum_linear_correction_matrix = main_methods.addInteractionDynamicsWithUpdate<
        LinearCorrectionMatrix>(continuum_inner, solver_parameters_.linear_correction_matrix_coeff_);

    auto &continuum_shear_force = addShearForceIntegration(entity_manager, main_methods, continuum_inner);

    auto &continuum_solid_contact_factor = main_methods.addInteractionDynamics<
        solid_dynamics::RepulsionFactor>(continuum_solid_contact);
    auto &continuum_solid_contact_force = main_methods.addInteractionDynamicsWithUpdate<
        solid_dynamics::RepulsionForceCK, Wall>(
        continuum_solid_contact, solver_parameters_.contact_numerical_damping_);
    //----------------------------------------------------------------------
    //	Define the methods for I/O operations, observations
    //	and regression tests of the simulation.
    //----------------------------------------------------------------------
    auto &body_state_recorder = main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);
    for (auto &solid_body : solid_bodies)
    {
        body_state_recorder.addToWrite<Vecd>(*solid_body, "NormalDirection");
        body_state_recorder.addToWrite<Vecd>(*solid_body, "Velocity");
    }
    body_state_recorder.addToWrite<Real>(continuum_body, "Pressure");
    body_state_recorder.addToWrite<Real>(continuum_body, "IntactFactor");
    //----------------------------------------------------------------------
    //	Define Preparation or initialization step for the time integration loop.
    //----------------------------------------------------------------------
    StagePipeline<InitializationHookPoint> &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialConditions);

            solid_cell_linked_list.exec();
            continuum_update_configuration.exec();

            continuum_advection_step_setup.exec();
            continuum_solid_contact_factor.exec();
            continuum_linear_correction_matrix.exec();

            body_state_recorder.writeToFile();
        });

    //----------------------------------------------------------------------
    //	Define time-integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(continuum_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(sim.getOutputInterval());
    time_stepper.setScreeningInterval(solver_parameters_.screen_interval_);
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
            Real dt = time_stepper.incrementPhysicalTime(continuum_acoustic_time_step);
            continuum_shear_force.exec(dt);
            continuum_solid_contact_force.exec();
            continuum_acoustic_step_1st_half.exec(dt);
            simulation_pipeline.run_hooks(SimulationHookPoint::BoundaryConditions);
            continuum_acoustic_step_2nd_half.exec(dt);

            if (time_stepper.isFirstComputingStep() || time_stepper.isScreeningStep())
            {
                std::cout << std::fixed << std::setprecision(9)
                          << "N=" << time_stepper.getIterationStep()
                          << "  Time = " << time_stepper.getPhysicalTime()
                          << "  advection_dt = " << advection_step.getInterval()
                          << "  acoustic_dt = " << time_stepper.getGlobalTimeStepSize()
                          << "\n";
            }
        });

    simulation_pipeline.main_steps.push_back( // advection step
        [&]()
        {
            if (advection_step(continuum_advection_time_step))
            {
                continuum_update_particle_position.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::PositionConstraints);
                time_stepper.incrementIterationStep();

                if (state_recording_trigger())
                {
                    body_state_recorder.writeToFile();
                }

                simulation_pipeline.run_hooks(SimulationHookPoint::ExtraOutputs);

                solid_cell_linked_list.exec();
                continuum_update_configuration.exec();
                continuum_advection_step_setup.exec();
                continuum_solid_contact_factor.exec();
                continuum_linear_correction_matrix.exec();
            }
        });

    //----------------------------------------------------------------------
    // Define optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------

    //----------------------------------------------------------------------
    // Initial condition from restart file if enabled.
    //----------------------------------------------------------------------
    RestartConfig &restart_config = sim.getRestartConfig();
    if (restart_config.enabled)
    {
        sph_system.setRestartStep(restart_config.restore_step);
        auto &restart_io = main_methods.addIODynamics<RestartIOCK>(sph_system);
        addOutputEvolvingVariablesBounds(main_methods, continuum_body);

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ExtraOutputs, [&]()
            { 
                if (time_stepper.getIterationStep() % restart_config.save_interval == 0)
                {
                    restart_io.writeToFile(time_stepper.getIterationStep());
                    outputEvolvingVariablesBounds();
                } });

        if (restart_config.restore_step != 0)
        {
            initialization_pipeline.insert_hook(
                InitializationHookPoint::InitialConditions, [&]()
                { 
                    time_stepper.setRestartStep(restart_config.restore_step);
                    restart_io.readRestartFiles(restart_config.restore_step); });
        }
    }

    //----------------------------------------------------------------------
    // Constraints carried at last due to possible third-party dependencies.
    //----------------------------------------------------------------------
    if (config.contains("body_constraints"))
    {
        ConstraintBuilder &constraint_builder =
            *entity_manager.emplaceEntity<ConstraintBuilder>("ConstraintBuilder");
        constraint_builder.addConstraints(sim, main_methods, config);
    }
}
//=================================================================================================//
void ContinuumSimulationBuilder::updateSolverParameters(SPHSimulation &sim, const json &config)
{
    if (config.contains("acoustic_cfl"))
        solver_parameters_.acoustic_cfl_ = config.at("acoustic_cfl").get<Real>();
    if (config.contains("advection_cfl"))
        solver_parameters_.advection_cfl_ = config.at("advection_cfl").get<Real>();
    if (config.contains("linear_correction_matrix_coeff"))
        solver_parameters_.linear_correction_matrix_coeff_ = config.at("linear_correction_matrix_coeff").get<Real>();
    if (config.contains("contact_numerical_damping"))
        solver_parameters_.contact_numerical_damping_ = config.at("contact_numerical_damping").get<Real>();
    if (config.contains("shear_stress_damping"))
        solver_parameters_.shear_stress_damping_ = config.at("shear_stress_damping").get<Real>();
    if (config.contains("hourglass_factor"))
        solver_parameters_.hourglass_factor_ = config.at("hourglass_factor").get<Real>();
    if (config.contains("screen_interval"))
        solver_parameters_.screen_interval_ = config.at("screen_interval").get<int>();
}
//=================================================================================================//
void ContinuumSimulationBuilder::outputEvolvingVariablesBounds()
{
    std::cout << "---------------------------------------------\n";
    for (UnsignedInt j = 0; j < output_evolving_variables_bounds_[0].size(); ++j)
    {
        std::pair<Real, UnsignedInt> bound = output_evolving_variables_bounds_[0][j]->exec();
        std::cout << std::fixed << std::setprecision(9)
                  << "Evolving scalar variable bound: " << evolving_variables_names_[0][j]
                  << " = " << bound.first << ", particle_index = " << bound.second << "\n";
    }
    std::cout << "---------------------------------------------\n";
    for (UnsignedInt j = 0; j < output_evolving_variables_bounds_[1].size(); ++j)
    {
        std::pair<Real, UnsignedInt> bound = output_evolving_variables_bounds_[1][j]->exec();
        std::cout << std::fixed << std::setprecision(9)
                  << "Evolving vector variable bound: " << evolving_variables_names_[1][j]
                  << " = " << bound.first << ", particle_index = " << bound.second << "\n";
    }
    std::cout << "---------------------------------------------\n";
    for (UnsignedInt j = 0; j < output_evolving_variables_bounds_[2].size(); ++j)
    {
        std::pair<Real, UnsignedInt> bound = output_evolving_variables_bounds_[2][j]->exec();
        std::cout << std::fixed << std::setprecision(9)
                  << "Evolving matrix variable bound: " << evolving_variables_names_[2][j]
                  << " = " << bound.first << ", particle_index = " << bound.second << "\n";
    }
    std::cout << "---------------------------------------------\n";
}
//=================================================================================================//
} // namespace SPH
