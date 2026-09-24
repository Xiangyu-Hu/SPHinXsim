#include "fluid_simulation_builder.h"

#include "base_simulation_builder.hpp"
#include "constraint_builder.h"
#include "fluid_dynamics_builder.hpp"
#include "solid_dynamics_builder.hpp"

#include "force_on_structure.h"
#include "thermal_dynamics_builder.hpp"
namespace SPH
{
using namespace fluid_dynamics;
//=================================================================================================//
void FluidSimulationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    // SPHSystem and entity manager.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = sim.defineSPHSystem(config);
    EntityManager &config_manager = sim.getConfigManager();
    SPHSolver &sph_solver = sim.defineSPHSolver(*this, config);
    //----------------------------------------------------------------------
    // Creating bodies with inital geometry, materials and particles.
    //----------------------------------------------------------------------
    buildFluidBodies(sph_system, config_manager, config.at("fluid_bodies"));
    buildSolidBodies(sph_system, config_manager, config.at("solid_bodies"));
    //----------------------------------------------------------------------
    // Define the main numerical methods used in the simulation.
    //----------------------------------------------------------------------
    auto &main_methods = sph_solver.getMainMethodContainer();
    RecordingBuilder::createBodyStatesRecording(sph_system, config_manager, main_methods);
    // Relations (inner + contacts, fluid and solid) are built by the shared
    // update-configuration step and registered for per-step updates, then
    // retrieved by name where needed below.
    buildUpdateConfiguration(sim, main_methods, config);
    //----------------------------------------------------------------------
    // Define dependent optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    FluidDynamicsBuilder::buildSurfaceIndicationIfOpenBoundary(sim, main_methods);
    //----------------------------------------------------------------------
    // The essential main methods used for the simulation.
    //----------------------------------------------------------------------
    SolidDynamicsBuilder::buildMaterialIdAssignmentIfPresent(sim, main_methods, config);
    SolidDynamicsBuilder::buildCompositeSolidsIfPresent(sim, main_methods, config);
    auto &fluid_advection_step_setup = FluidDynamicsBuilder::addAdvectionStepSetup(sim, main_methods);
    auto &fluid_particle_position = FluidDynamicsBuilder::addUpdateParticlePosition(sim, main_methods);

    auto &fluid_linear_correction_matrix = FluidDynamicsBuilder::addLinearCorrectionMatrix(sim, main_methods);

    auto &fluid_acoustic_step_1st_half = FluidDynamicsBuilder::addAcousticStep1stHalf(sim, main_methods);
    auto &fluid_acoustic_step_2nd_half = FluidDynamicsBuilder::addAcousticStep2ndHalf(sim, main_methods);

    // Coupling forces the fluid exerts on each composite structure. The
    // structure-fluid contact is retrieved by name from the relations built
    // by buildUpdateConfiguration.
    for (const auto &solid_config : config.at("solid_bodies"))
    {
        if (solid_config.at("material").at("type").get<std::string>() != "composite_solid")
            continue;
        std::string body_name = solid_config.at("name").get<std::string>();
        auto &fluid_body_local = *sph_system.collectBodies<FluidBody>().front();
        auto &structure_contact = sph_system.getRelationByName<Contact<Relation<SolidBody, FluidBody>>>(
            body_name + fluid_body_local.Name());

        auto &viscous_force_on_structure =
            main_methods.addInteractionDynamics<FSI::ViscousForceFromFluid<Contact<WithUpdate, Viscosity, NoKernelCorrectionCK, Relation<SolidBody, FluidBody>>>>(structure_contact);
        auto &pressure_force_on_structure =
            main_methods.addInteractionDynamics<FSI::PressureForceFromFluid<Contact<WithUpdate, AcousticRiemannSolverCK, NoKernelCorrectionCK, Relation<SolidBody, FluidBody>>>>(structure_contact);

        sim.getInitializationPipeline().insert_hook(
            InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [&]()
            { viscous_force_on_structure.exec(); });

        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            { pressure_force_on_structure.exec(); });

        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { viscous_force_on_structure.exec(); });
    }

    auto &fluid_density_regularization = FluidDynamicsBuilder::addDensityRegularization(sim, main_methods);

    auto &fluid_advection_time_step = FluidDynamicsBuilder::addAdvectionTimeStep(sim, main_methods);
    auto &fluid_acoustic_time_step = FluidDynamicsBuilder::addAcousticTimeStep(sim, main_methods);
    //----------------------------------------------------------------------
    // Define dependent optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    buildStartupAccelerationIfPresent(sim, main_methods, config);
    buildExternalForceIfPresent(sim, main_methods, config);
    FluidDynamicsBuilder::buildTransportVelocityFormulationIfNotFreeSurface(sim, main_methods);
    FluidDynamicsBuilder::buildViscousForceIfPresent(sim, main_methods);
    ThermalDynamicsBuilder::buildThermalDynamicsIfPresent(sim, main_methods);
    //----------------------------------------------------------------------
    // Define initial and boundary conditions, particle deletion and sorting.
    //----------------------------------------------------------------------
    ConstraintBuilder::buildConstraintsIfPresent(sim, main_methods, config);
    buildInitialConditionIfPresent(sim, main_methods, config);
    buildRestartFromFileIfPresent(sim, main_methods, config);
    FluidDynamicsBuilder::buildBoundaryConditionsIfPresent(sim, main_methods, config);
    FluidDynamicsBuilder::buildParticleDeletionIfPresent(sim, main_methods);
    FluidDynamicsBuilder::buildParticleSortIfPresent(sim, main_methods);
    //----------------------------------------------------------------------
    // Define state recording for visualization the simulation results.
    //----------------------------------------------------------------------
    RecordingBuilder::finalizeBodyStatesRecording(sph_system, config_manager, config);
    RecordingBuilder::buildObservationIfPresent(sim, main_methods, config);
    RecordingBuilder::buildEnergyRecordingIfPresent(sim, main_methods, config);
    auto &body_state_recorder = RecordingBuilder::getBodyStatesRecording(config_manager);
    //----------------------------------------------------------------------
    //	Define time integration method, screen output and observation sample rate.
    //----------------------------------------------------------------------
    auto &solver_common_config = config_manager.getEntity<SolverCommonConfig>("SolverCommonConfig");
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(fluid_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(solver_common_config.output_interval_);
    time_stepper.setScreeningInterval(solver_common_config.screen_interval_);
    time_stepper.setObservationInterval(solver_common_config.observation_interval_);
    //----------------------------------------------------------------------
    //	Define preparation or initialization step before the main integration.
    //----------------------------------------------------------------------
    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialUpdateConfiguration);

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialCondition);
            initialization_pipeline.run_hooks(InitializationHookPoint::AfterInitialCondition);

            initialization_pipeline.run_hooks(InitializationHookPoint::RestartFromFile);
            initialization_pipeline.run_hooks(InitializationHookPoint::UpdateConfigurationAfterRestart);

            fluid_density_regularization.exec();
            fluid_advection_step_setup.exec();
            fluid_linear_correction_matrix.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialAfterLinearCorrectionMatrix);

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialObservation);
            body_state_recorder.writeToFile();

            initialization_pipeline.run_hooks(InitializationHookPoint::PreSimulationSanityCheck);
        });
    //----------------------------------------------------------------------
    // Define the time integration method (dual acoustic/advection stepping).
    //----------------------------------------------------------------------
    auto &simulation_pipeline = sim.getSimulationPipeline();

    simulation_pipeline.main_steps.push_back(
        [&]()
        {
            Real dt = time_stepper.incrementPhysicalTime(fluid_acoustic_time_step);
            fluid_acoustic_step_1st_half.exec(dt);
            simulation_pipeline.run_hooks(SimulationHookPoint::BoundaryCondition);
            fluid_acoustic_step_2nd_half.exec(dt);
            simulation_pipeline.run_hooks(SimulationHookPoint::CouplingSynchronization);
        });

    simulation_pipeline.main_steps.push_back( // advection or particle configuration step
        [&]()
        {
            if (advection_step(fluid_advection_time_step))
            {
                fluid_particle_position.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::PositionConstraint);
                time_stepper.incrementIterationStep();

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

                simulation_pipeline.run_hooks(SimulationHookPoint::ExtraOutput);

                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleSort);

                simulation_pipeline.run_hooks(SimulationHookPoint::UpdateConfiguration);
                simulation_pipeline.run_hooks(SimulationHookPoint::AfterUpdateConfiguration);
                fluid_density_regularization.exec();
                fluid_advection_step_setup.exec();
                fluid_linear_correction_matrix.exec();
                simulation_pipeline.run_hooks(SimulationHookPoint::AfterLinearCorrectionMatrix);
            }
        });
}
//=================================================================================================//
void FluidSimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    SimulationBuilder::parseSolverParameters(config_manager, config);
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    if (config.contains("fluid_dynamics"))
    {
        config_manager.emplaceEntity<FluidSolverConfig>(
            "FluidSolverConfig", parseFluidSolverConfig(scaling_config, config.at("fluid_dynamics")));
    }
}
//=================================================================================================//
FluidSolverConfig FluidSimulationBuilder::parseFluidSolverConfig(
    const ScalingConfig &scaling_config, const json &config)
{
    FluidSolverConfig params;
    if (config.contains("acoustic_cfl"))
        params.acoustic_cfl_ = scaling_config.jsonToReal(
            config.at("acoustic_cfl"), "Dimensionless");
    if (config.contains("advection_cfl"))
        params.advection_cfl_ = scaling_config.jsonToReal(
            config.at("advection_cfl"), "Dimensionless");
    if (config.contains("max_velocity_factor"))
        params.max_velocity_factor_ = scaling_config.jsonToReal(
            config.at("max_velocity_factor"), "Dimensionless");
    if (config.contains("surface_type"))
        params.surface_type_ = config.at("surface_type").get<std::string>();
    if (config.contains("kernel_correction"))
        params.kernel_correction_ = config.at("kernel_correction").get<std::string>();
    if (config.contains("particle_sort_frequency"))
    {
        params.particle_sorting_ = true;
        params.sort_frequency_ = config.at("particle_sort_frequency").get<UnsignedInt>();
    }
    return params;
}
//=================================================================================================//
} // namespace SPH
