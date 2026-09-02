#include "continuum_simulation_builder.h"

#include "constraint_builder.h"
#include "recording_builder.h"
#include "sph_simulation.h"

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
    SPHSolver &sph_solver = sim.defineSPHSolver(*this, config);
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
    buildContinuumBodies(sph_system, config_manager, config.at("continuum_bodies"));
    buildSolidBodies(sph_system, config_manager, config.at("solid_bodies"));
    //----------------------------------------------------------------------
    // Define the main numerical methods used in the simulation.
    // Note that there may be data dependence on the sequence of constructions.
    // Generally, the configuration dynamics, such as update cell linked list,
    // update body relations, are defined first.
    //----------------------------------------------------------------------
    auto &main_methods = sph_solver.getMainMethodContainer();
    RecordingBuilder::createBodyStatesRecording(sph_system, config_manager, main_methods);
    buildUpdateConfiguration(sim, main_methods, config);

    auto &continuum_advection_step_setup = ContinuumDynamicsBuilder::addAdvectionStepSetup(sim, main_methods);
    auto &continuum_update_particle_position = ContinuumDynamicsBuilder::addUpdateParticlePosition(sim, main_methods);

    auto &continuum_acoustic_step_1st_half = ContinuumDynamicsBuilder::addAcousticStep1stHalf(sim, main_methods);
    auto &continuum_acoustic_step_2nd_half = ContinuumDynamicsBuilder::addAcousticStep2ndHalf(sim, main_methods);

    auto &continuum_linear_correction_matrix = ContinuumDynamicsBuilder::addLinearCorrectionMatrix(sim, main_methods);
    //----------------------------------------------------------------------
    // Optional methods that depend on the presence of certain features in the simulation.
    //----------------------------------------------------------------------
    ContinuumDynamicsBuilder::buildShearForceIntegrationIfPresent(sim, main_methods);
    ContinuumDynamicsBuilder::buildContactRepulsionIfPresent(sim, main_methods, config);
    ContinuumDynamicsBuilder::buildDensityRegularizationIfPresent(sim, main_methods);
    ContinuumDynamicsBuilder::buildStressDiffusionIfPresent(sim, main_methods);
    //----------------------------------------------------------------------
    // Initial condition if present.
    //----------------------------------------------------------------------
    buildInitialConditionIfPresent(sim, main_methods, config);
    buildRestartFromFileIfPresent(sim, main_methods, config);
    //----------------------------------------------------------------------
    // Constraints carried at last due to possible third-party dependencies.
    //----------------------------------------------------------------------
    ConstraintBuilder::buildConstraintsIfPresent(sim, main_methods, config);
    buildExternalForceIfPresent(sim, main_methods, config);
    RecordingBuilder::buildObservationIfPresent(sim, main_methods, config);
    //----------------------------------------------------------------------
    // Finalize state recording for visualization the simulation results,
    // including extra state variables and applying scaling
    //----------------------------------------------------------------------
    RecordingBuilder::finalizeBodyStatesRecording(sph_system, config_manager, config);
    auto &body_state_recorder = RecordingBuilder::getBodyStatesRecording(config_manager);
    //----------------------------------------------------------------------
    //	Define time-integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &continuum_advection_time_step = ContinuumDynamicsBuilder::addAdvectionTimeStep(sim, main_methods);
    auto &continuum_acoustic_time_step = ContinuumDynamicsBuilder::addAcousticTimeStep(sim, main_methods);
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
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialUpdateConfiguration);

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialCondition);
            initialization_pipeline.run_hooks(InitializationHookPoint::AfterInitialCondition);

            initialization_pipeline.run_hooks(InitializationHookPoint::RestartFromFile);
            initialization_pipeline.run_hooks(InitializationHookPoint::UpdateConfigurationAfterRestart);

            continuum_advection_step_setup.exec();
            continuum_linear_correction_matrix.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialAfterLinearCorrectionMatrix);

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialObservation);
            body_state_recorder.writeToFile();

            initialization_pipeline.run_hooks(InitializationHookPoint::PreSimulationSanityCheck);
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
            simulation_pipeline.run_hooks(SimulationHookPoint::BeforeMainPhysicalTimeStep);
            continuum_acoustic_step_1st_half.exec(dt);
            simulation_pipeline.run_hooks(SimulationHookPoint::BoundaryCondition);
            continuum_acoustic_step_2nd_half.exec(dt);

            if (time_stepper.isFirstComputingStep() || time_stepper.isScreeningStep())
            {
                std::cout << std::fixed << std::setprecision(9)
                          << "N=" << time_stepper.getIterationStep()
                          << "  Time = " << time_stepper.getPhysicalTimeWithScalingRef()
                          << "  advection_dt = " << advection_step.getIntervalWithScalingRef()
                          << "(scaled: " << advection_step.getInterval() << "),"
                          << "  acoustic_dt = " << time_stepper.getGlobalTimeStepSizeWithScalingRef()
                          << "(scaled: " << time_stepper.getGlobalTimeStepSize() << ")"
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

                simulation_pipeline.run_hooks(SimulationHookPoint::UpdateConfiguration);
                simulation_pipeline.run_hooks(SimulationHookPoint::AfterUpdateConfiguration);
                continuum_advection_step_setup.exec();
                continuum_linear_correction_matrix.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::AfterLinearCorrectionMatrix);
            }
        });
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
    if (config.contains("plastic_riemann_dissipation_factor"))
        parameters.plastic_riemann_dissipation_factor_ = scaling_config.jsonToReal(
            config.at("plastic_riemann_dissipation_factor"), "Dimensionless");
    if (config.contains("surface_type"))
        parameters.surface_type_ = config.at("surface_type").get<std::string>();

    return parameters;
}
//=================================================================================================//
} // namespace SPH
