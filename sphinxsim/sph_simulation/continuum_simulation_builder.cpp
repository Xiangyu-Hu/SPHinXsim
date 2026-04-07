#include "continuum_simulation_builder.h"

#include "sph_simulation.h"
namespace SPH
{
//=================================================================================================//
void ContinuumSimulationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = sim.defineSPHSystem(config);
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
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
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

    auto &continuum_acoustic_step_1st_half = main_methods.addInteractionDynamics<
        fluid_dynamics::AcousticStep1stHalf, OneLevel,
        AcousticRiemannSolverCK, NoKernelCorrectionCK>(continuum_inner);

    auto &continuum_acoustic_step_2nd_half = main_methods.addInteractionDynamics<
        fluid_dynamics::AcousticStep2ndHalf, OneLevel,
        AcousticRiemannSolverCK, NoKernelCorrectionCK>(continuum_inner);

    Fluid &continuum_eos = DynamicCast<Fluid>(this, continuum_body.getBaseMaterial());
    const Real U_ref = continuum_eos.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    auto &continuum_advection_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AdvectionTimeStepCK>(continuum_body, U_ref, solver_parameters_.advection_cfl);
    auto &continuum_acoustic_time_step = main_methods.addReduceDynamics<
        fluid_dynamics::AcousticTimeStepCK<>>(continuum_body, solver_parameters_.acoustic_cfl);

    auto &continuum_linear_correction_matrix = main_methods.addInteractionDynamicsWithUpdate<
        LinearCorrectionMatrix>(continuum_inner);
    auto &column_shear_force =
        main_methods.addParticleDynamicsGroup()
            .add(&main_methods.addInteractionDynamics<LinearGradient, Vecd>(continuum_inner, "Velocity"))
            .add(&main_methods.addInteractionDynamicsOneLevel<
                  continuum_dynamics::ShearIntegration, J2Plasticity>(continuum_inner));

    auto &continuum_solid_contact_factor = main_methods.addInteractionDynamics<
        solid_dynamics::RepulsionFactor>(continuum_solid_contact);
    auto &continuum_solid_contact_force = main_methods.addInteractionDynamicsWithUpdate<
        solid_dynamics::RepulsionForceCK, Wall>(continuum_solid_contact);
    //----------------------------------------------------------------------
    //	Define the methods for I/O operations, observations
    //	and regression tests of the simulation.
    //----------------------------------------------------------------------
    auto &body_state_recorder = main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);
    for (auto &solid_body : solid_bodies)
    {
        body_state_recorder.addToWrite<Vecd>(*solid_body, "NormalDirection");
    }
    body_state_recorder.addToWrite<Real>(continuum_body, "Pressure");
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
            continuum_linear_correction_matrix.exec();

            body_state_recorder.writeToFile();
        });

    //----------------------------------------------------------------------
    //	Define time-integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(continuum_advection_time_step.exec());
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
            Real dt = time_stepper.incrementPhysicalTime(continuum_acoustic_time_step);
            simulation_pipeline.run_hooks(SimulationHookPoint::ForcePrior);
            continuum_acoustic_step_1st_half.exec(dt);
            continuum_acoustic_step_2nd_half.exec(dt);
        });

    simulation_pipeline.main_steps.push_back( // advection step
        [&, screening_interval, observation_interval]()
        {
            if (advection_step(continuum_advection_time_step))
            {
                continuum_update_particle_position.exec();

                if (advection_steps_ % screening_interval == 0)
                {
                    std::cout << std::fixed << std::setprecision(9)
                              << "N=" << advection_steps_
                              << "  Time = " << time_stepper.getPhysicalTime()
                              << "  advection_dt = " << advection_step.getInterval()
                              << "  acoustic_dt = " << time_stepper.getGlobalTimeStepSize()
                              << "\n";
                }

                if (state_recording_trigger())
                {
                    body_state_recorder.writeToFile();
                }

                solid_cell_linked_list.exec();
                continuum_update_configuration.exec();
                continuum_advection_step_setup.exec();
                continuum_linear_correction_matrix.exec();
                advection_steps_++;
            }
        });
}
//=================================================================================================//
void ContinuumSimulationBuilder::updateSolverParameters(SPHSimulation &sim, const json &config)
{
    if (config.contains("acoustic_cfl"))
        solver_parameters_.acoustic_cfl = config.at("acoustic_cfl").get<Real>();
    if (config.contains("advection_cfl"))
        solver_parameters_.advection_cfl = config.at("advection_cfl").get<Real>();
}
//=================================================================================================//
} // namespace SPH
