#include "continuum_simulation_builder.hpp"

#include "base_simulation_builder.hpp"
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
    EntityManager &config_manager = sim.getConfigManager();
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
    buildContinuumBodies(sph_system, config_manager, config.at("continuum_bodies"));
    buildSolidBodies(sph_system, config_manager, config.at("solid_bodies"));
    //----------------------------------------------------------------------
    //	Define body relation map.
    //	The relations give the topological connections within a body
    //  or with other bodies within interaction range.
    //  Generally, we first define all the inner relations, then the contact relations.
    //----------------------------------------------------------------------
    auto &continuum_body = *sph_system.collectBodies<RealBody>().front(); // assume only one continuum body for now
    StdVec<SolidBody *> solid_bodies = sph_system.collectBodies<SolidBody>();

    auto &continuum_inner = sph_system.addInnerRelation(continuum_body);
    auto &continuum_solid_contact = sph_system.addContactRelation(continuum_body, solid_bodies);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = sim.defineSPHSolver(*this, config);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    //    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
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

    auto &continuum_acoustic_step_1st_half = addAcousticStep1stHalf(config_manager, main_methods, continuum_inner);
    auto &continuum_acoustic_step_2nd_half = addAcousticStep2ndHalf(config_manager, main_methods, continuum_inner);

    auto &continuum_solver_parameters = config_manager.getEntity<
        ContinuumSolverParameters>("ContinuumSolverParameters");
    Fluid &continuum_eos = DynamicCast<Fluid>(this, continuum_body.getBaseMaterial());
    const Real U_ref = continuum_eos.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    auto &continuum_advection_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AdvectionTimeStepCK>(continuum_body, U_ref, continuum_solver_parameters.advection_cfl_);
    auto &continuum_acoustic_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AcousticTimeStepCK<>>(continuum_body, continuum_solver_parameters.acoustic_cfl_);

    auto &continuum_linear_correction_matrix = main_methods.addInteractionDynamicsWithUpdate<
        LinearCorrectionMatrix>(continuum_inner, continuum_solver_parameters.linear_correction_matrix_coeff_);

    auto &continuum_shear_force = addShearForceIntegration(config_manager, main_methods, continuum_inner);

    auto &continuum_solid_contact_factor = main_methods.addInteractionDynamics<
        solid_dynamics::RepulsionFactor>(continuum_solid_contact);
    auto &continuum_solid_contact_force = main_methods.addInteractionDynamicsWithUpdate<
        solid_dynamics::RepulsionForceCK, Wall>(
        continuum_solid_contact, continuum_solver_parameters.contact_numerical_damping_);
    //----------------------------------------------------------------------
    // Define basic state recording for visualization the simulation results.
    //----------------------------------------------------------------------
    auto &body_state_recorder = createBodyStatesRecording(sph_system, config_manager, main_methods, config);
    //----------------------------------------------------------------------
    //	Define time-integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &solver_common_config = config_manager.getEntity<SolverCommonConfig>("SolverCommonConfig");
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(continuum_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(solver_common_config.output_interval_);
    time_stepper.setScreeningInterval(solver_common_config.screen_interval_);
    //----------------------------------------------------------------------
    //	Define Preparation or initialization step for the time integration loop.
    //----------------------------------------------------------------------
    StagePipeline<InitializationHookPoint> &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialCondition);

            solid_cell_linked_list.exec();
            continuum_update_configuration.exec();

            continuum_advection_step_setup.exec();
            continuum_solid_contact_factor.exec();
            continuum_linear_correction_matrix.exec();

            body_state_recorder.writeToFile();
        });
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
            simulation_pipeline.run_hooks(SimulationHookPoint::BoundaryCondition);
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
                simulation_pipeline.run_hooks(SimulationHookPoint::PositionConstraint);
                time_stepper.incrementIterationStep();

                if (state_recording_trigger())
                {
                    body_state_recorder.writeToFile();
                }

                simulation_pipeline.run_hooks(SimulationHookPoint::ExtraOutput);

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
    auto &restart_config = config_manager.getEntity<RestartConfig>("RestartConfig");
    if (restart_config.enabled_)
    {
        sph_system.setRestartStep(restart_config.restore_step_);
        auto &restart_io = main_methods.addIODynamics<RestartIOCK>(
            sph_system, restart_config.summary_enabled_);

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ExtraOutput, [&]()
            { 
                if (time_stepper.getIterationStep() % restart_config.save_interval_ == 0)
                {
                    restart_io.writeToFile(time_stepper.getIterationStep());
                } });

        if (restart_config.restore_step_ != 0)
        {
            initialization_pipeline.insert_hook(
                InitializationHookPoint::InitialCondition, [&]()
                { 
                    time_stepper.setRestartStep(restart_config.restore_step_);
                    restart_io.readRestartFiles(restart_config.restore_step_); });
        }
    }

    //----------------------------------------------------------------------
    // Constraints carried at last due to possible third-party dependencies.
    //----------------------------------------------------------------------
    if (config.contains("body_constraints"))
    {
        ConstraintBuilder &constraint_builder =
            *config_manager.emplaceEntity<ConstraintBuilder>("ConstraintBuilder");
        constraint_builder.addConstraints(sim, main_methods, config);
    }
}
//=================================================================================================//
void ContinuumSimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    SimulationBuilder::parseSolverParameters(config_manager, config);
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    if (config.contains("continuum_dynamics"))
    {
        config_manager.emplaceEntity<ContinuumSolverParameters>(
            "ContinuumSolverParameters",
            parseContinuumSolverParameters(scaling_config, config.at("continuum_dynamics")));
    }
}
//=================================================================================================//
ContinuumSolverParameters ContinuumSimulationBuilder::parseContinuumSolverParameters(
    const ScalingConfig &scaling_config, const json &config)
{
    ContinuumSolverParameters parameters;
    if (config.contains("acoustic_cfl"))
        parameters.acoustic_cfl_ = scaling_config.jsonToReal(
            config.at("acoustic_cfl"), "Dimensionless");
    if (config.contains("advection_cfl"))
        parameters.advection_cfl_ = scaling_config.jsonToReal(
            config.at("advection_cfl"), "Dimensionless");
    if (config.contains("linear_correction_matrix_coeff"))
        parameters.linear_correction_matrix_coeff_ = scaling_config.jsonToReal(
            config.at("linear_correction_matrix_coeff"), "Dimensionless");
    if (config.contains("contact_numerical_damping"))
        parameters.contact_numerical_damping_ = scaling_config.jsonToReal(
            config.at("contact_numerical_damping"), "Dimensionless");
    if (config.contains("shear_stress_damping"))
        parameters.shear_stress_damping_ = scaling_config.jsonToReal(
            config.at("shear_stress_damping"), "Dimensionless");
    if (config.contains("hourglass_factor"))
        parameters.hourglass_factor_ = scaling_config.jsonToReal(
            config.at("hourglass_factor"), "Dimensionless");

    return parameters;
}
//=================================================================================================//
} // namespace SPH
