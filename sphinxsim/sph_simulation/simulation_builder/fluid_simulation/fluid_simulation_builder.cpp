#include "fluid_simulation_builder.hpp"

#include "base_simulation_builder.hpp"
#include "fluid_dynamics_builder.hpp"
#include "solid_dynamics_builder.hpp"

#include "composite_solid.h"
#include "force_on_structure.h"
#include "region_shape_material_id.h"
#include "structure_surface_motion.h"
#include "traveling_wave_active_strain.h"
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
    SPHSolver &sph_solver = sim.defineSPHSolver(*this, config);
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
    // Structure to fluid contacts are created here, alongside the other relations, because
    // a contact built later does not survive the subsequent body and dynamics construction.
    StdVec<Contact<> *> structure_fluid_contacts;
    for (const auto &solid_config : config.at("solid_bodies"))
    {
        if (solid_config.at("material").at("type").get<std::string>() != "composite_solid")
            continue;
        std::string body_name = solid_config.at("name").get<std::string>();
        RealBody &structure_body = sph_system.getBodyByName<RealBody>(body_name);
        structure_fluid_contacts.push_back(
            &sim.addStructureContact(structure_body, StdVec<RealBody *>{&fluid_body}));
    }
    //----------------------------------------------------------------------
    // Define the main numerical methods used in the simulation.
    // Note that there may be data dependence on the sequence of constructions.
    //----------------------------------------------------------------------
    auto &main_methods = sph_solver.getMainMethodContainer();
    RecordingBuilder::createBodyStatesRecording(sph_system, config_manager, main_methods);
    //----------------------------------------------------------------------
    // Define dependent optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    buildSurfaceIndicationIfOpenBoundary(sim, main_methods, fluid_inner, fluid_wall_contact);
    //----------------------------------------------------------------------
    // The essential main methods used for the simulation.
    // Generally, the configuration dynamics, such as update cell linked list,
    // update body relations, are defined first.
    //----------------------------------------------------------------------
    auto &solid_cell_linked_list = main_methods.addCellLinkedListDynamics(solid_bodies);
    // Optional per particle material id assignment, driven by the region model
    // given in each solid body configuration.
    auto &material_id_assignment = main_methods.addParticleDynamicsGroup();
    bool has_material_id_assignment = false;
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    for (const auto &solid_config : config.at("solid_bodies"))
    {
        const json &material_config = solid_config.at("material");
        if (!material_config.contains("material_id_regions"))
            continue;

        const json &region_config = material_config.at("material_id_regions");
        std::string body_name = solid_config.at("name").get<std::string>();
        SolidBody *target_body = nullptr;
        for (SolidBody *solid_body : solid_bodies)
        {
            if (solid_body->Name() == body_name)
                target_body = solid_body;
        }
        if (target_body == nullptr)
        {
            throw std::runtime_error("material id regions refer to an unknown solid body: " + body_name);
        }

        StdVec<Shape *> region_shapes;
        StdVec<int> region_ids;
        for (const auto &region : region_config.at("regions"))
        {
            std::string shape_name = region.at("shape").get<std::string>();
            region_shapes.push_back(&config_manager.getEntity<Shape>(shape_name));
            region_ids.push_back(region.at("id").get<int>());
        }
        int default_id = region_config.at("default_id").get<int>();

        material_id_assignment.add(&main_methods.addStateDynamics<RegionShapeMaterialId>(
            *target_body, region_shapes, region_ids, default_id));
        has_material_id_assignment = true;
    }

    if (has_material_id_assignment)
    {
        sim.getInitializationPipeline().insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { material_id_assignment.exec(); });
    }
    // Structure contacts kept for the coupling forces, which can only be built
    // once the fluid has registered its own kinematic variables.
    // StdVec here would be a stack-use-after-return once buildSimulation()
    // returns and the pipeline starts invoking that lambda from stepTo().
    StdVec<ParticleDynamicsGroup *> &structure_configurations = *(new StdVec<ParticleDynamicsGroup *>());
    // Elastic solid bodies get their own configuration and stress relaxation.
    // Bodies declared rigid are skipped, so purely rigid cases are unaffected.
    size_t structure_index = 0;
    for (const auto &solid_config : config.at("solid_bodies"))
    {
        const std::string material_type =
            solid_config.at("material").at("type").get<std::string>();

        if (material_type != "composite_solid")
            continue;

        std::string body_name = solid_config.at("name").get<std::string>();
        RealBody &elastic_body = sph_system.getBodyByName<RealBody>(body_name);

        // The structure formulation is total Lagrangian: neighbours are found
        // once in the reference configuration and stay fixed.
        auto &elastic_inner = sph_system.addInnerRelation(elastic_body, ConfigType::Lagrangian);
        Contact<> &elastic_fluid_contact = *structure_fluid_contacts[structure_index++];

        auto &initialize_displacement =
            main_methods.addStateDynamics<InitializeDisplacementCK>(elastic_body);

        auto &update_average_velocity =
            main_methods.addStateDynamics<UpdateAverageVelocityAndAccelerationCK>(elastic_body);

        auto &elastic_configuration =
            main_methods.addParticleDynamicsGroup()
                .add(&main_methods.addCellLinkedListDynamics(elastic_body))
                .add(&main_methods.addRelationDynamics(elastic_inner))
                .add(&main_methods.addRelationDynamics(elastic_fluid_contact));

        // Snapshot the surface before the structure advances.
        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { initialize_displacement.exec(); });

        const json &material_config = solid_config.at("material");
        std::function<void()> active_strain_pre_substep_hook = nullptr;
        if (material_config.contains("active_strain"))
        {
            const json &wave_config = material_config.at("active_strain");

            Vecd wave_center = Vecd::Zero();
            for (int k = 0; k != wave_center.size(); ++k)
            {
                wave_center[k] = scaling_config.jsonToReal(wave_config.at("center").at(k), "Length");
            }
            Real wave_span = scaling_config.jsonToReal(wave_config.at("region_span"), "Length");
            Real wave_core = scaling_config.jsonToReal(wave_config.at("core_thickness"), "Length");
            // Wave parameters are taken as given; they are not unit scaled.
            Real amplitude = wave_config.at("amplitude").get<Real>();
            Real frequency = wave_config.at("frequency").get<Real>();
            Real wavelength_factor = wave_config.at("wavelength_factor").get<Real>();
            Real start_time = wave_config.at("start_time").get<Real>();

            auto &active_strain = main_methods.addStateDynamics<TravelingWaveActiveStrain>(
                elastic_body, wave_center, wave_span, wave_core,
                amplitude, frequency, wavelength_factor, start_time);

            // SYCL reference imposes the active strain once per solid sub-step
            // (2d_flow_stream_around_fish_sycl.cpp:309), not once per coupling
            // interval, so the traveling wave phase stays current across
            // sub-steps. Passed to buildSolidDynamics below.
            active_strain_pre_substep_hook = [&active_strain]()
            { active_strain.exec(); };
        }

        auto &elastic_correction_matrix =
            SolidDynamicsBuilder::buildSolidDynamics<CompositeSolidMaterial>(
                sim, main_methods, elastic_inner, active_strain_pre_substep_hook);

        // Recover the averaged surface motion the fluid sees over the interval,
        // after the structure sub loop has advanced.
        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { update_average_velocity.exec(sph_solver.getTimeStepper().getGlobalTimeStepSize()); });

        auto &elastic_normal_direction =
            main_methods.addStateDynamics<solid_dynamics::UpdateElasticNormalDirectionCK>(elastic_body);

        // SYCL reference refreshes the elastic normal every advection step
        // (2d_flow_stream_around_fish_sycl.cpp:332); without this the normal
        // stays frozen at its t=0 value while the surface deforms, which
        // distorts the pressure force most where the motion is largest (tail).
        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { elastic_normal_direction.exec(); });

        auto &elastic_contact_update =
            main_methods.addParticleDynamicsGroup()
                .add(&main_methods.addCellLinkedListDynamics(elastic_body))
                .add(&main_methods.addRelationDynamics(elastic_fluid_contact));

        structure_configurations.push_back(&elastic_contact_update);

        sim.getInitializationPipeline().insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            {
                elastic_configuration.exec();
                elastic_correction_matrix.exec();
                elastic_normal_direction.exec(); });
    }
    auto &fluid_configuration =
        main_methods.addParticleDynamicsGroup()
            .add(&main_methods.addCellLinkedListDynamics(fluid_body))
            .add(&main_methods.addRelationDynamics(fluid_inner, fluid_wall_contact));

    auto &fluid_advection_step_setup = FluidDynamicsBuilder::addAdvectionStepSetup(sim, main_methods);
    auto &fluid_particle_position = FluidDynamicsBuilder::addUpdateParticlePosition(sim, main_methods);

    auto &fluid_linear_correction_matrix = addLinearCorrectionMatrixWithScope(
        config_manager, main_methods, fluid_inner, fluid_wall_contact);

    auto &fluid_acoustic_step_1st_half = FluidDynamicsBuilder::addAcousticStep1stHalf(sim, main_methods);
    auto &fluid_acoustic_step_2nd_half = FluidDynamicsBuilder::addAcousticStep2ndHalf(sim, main_methods);

    // Coupling forces the fluid exerts on each structure.
    for (Contact<> *structure_contact : structure_fluid_contacts)
    {
        auto &viscous_force_on_structure =
            main_methods.addInteractionDynamics<FSI::ViscousForceOnStructure<fluid_dynamics::ViscousForceCK<Contact<Wall, Viscosity, NoKernelCorrectionCK>>>>(*structure_contact);
        auto &pressure_force_on_structure =
            main_methods.addInteractionDynamics<FSI::PressureForceOnStructure<fluid_dynamics::AcousticStep2ndHalf<Contact<Wall, AcousticRiemannSolverCK, NoKernelCorrectionCK>>>>(*structure_contact);

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

    auto &fluid_density_regularization = addDensityRegularization(
        sim, main_methods, fluid_inner, fluid_wall_contact);

    auto &fluid_advection_time_step = FluidDynamicsBuilder::addAdvectionTimeStep(sim, main_methods);
    auto &fluid_acoustic_time_step = FluidDynamicsBuilder::addAcousticTimeStep(sim, main_methods);
    //----------------------------------------------------------------------
    //	Define time integration method, screen out uput and observation sample rate.
    //----------------------------------------------------------------------
    auto &solver_common_config = config_manager.getEntity<SolverCommonConfig>("SolverCommonConfig");
    auto &time_stepper = sph_solver.getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(fluid_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(solver_common_config.output_interval_);
    time_stepper.setScreeningInterval(solver_common_config.screen_interval_);
    time_stepper.setObservationInterval(solver_common_config.observation_interval_);
    //----------------------------------------------------------------------
    // Define dependent optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    buildExternalForceIfPresent(sim, main_methods, config);
    buildTransportVelocityFormulationIfNotFreeSurface(sim, main_methods, fluid_inner, fluid_wall_contact);
    buildViscousForceIfPresent(sim, main_methods, fluid_inner, fluid_wall_contact);
    ThermalDynamicsBuilder::buildThermalDynamicsIfPresent(sim, main_methods, fluid_inner, fluid_wall_contact);
    //----------------------------------------------------------------------
    // Define initial and boundary conditions,
    // particle deletion and sorting if present.
    //----------------------------------------------------------------------
    buildInitialConditionIfPresent(sim, main_methods, config);
    FluidDynamicsBuilder::buildBoundaryConditionsIfPresent(sim, main_methods, config);
    buildParticleDeletionIfPresent(sim, main_methods, fluid_body);
    buildParticleSortIfPresent(sim, main_methods, fluid_body);
    //----------------------------------------------------------------------
    // Define state recording for visualization the simulation results.
    //----------------------------------------------------------------------
    RecordingBuilder::finalizeBodyStatesRecording(sph_system, config_manager, config);
    RecordingBuilder::buildObservationIfPresent(sim, main_methods, config);
    RecordingBuilder::buildEnergyRecordingIfPresent(sim, main_methods, config);
    auto &body_state_recorder = RecordingBuilder::getBodyStatesRecording(config_manager);
    //----------------------------------------------------------------------
    //	Define preparation or initialization step before the main integration.
    //----------------------------------------------------------------------
    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            solid_cell_linked_list.exec();
            fluid_configuration.exec();

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialCondition);
            initialization_pipeline.run_hooks(InitializationHookPoint::AfterInitialCondition);

            fluid_density_regularization.exec();
            fluid_advection_step_setup.exec();
            fluid_linear_correction_matrix.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialAfterLinearCorrectionMatrix);

            initialization_pipeline.run_hooks(InitializationHookPoint::InitialObservation);
            body_state_recorder.writeToFile();

            initialization_pipeline.run_hooks(InitializationHookPoint::PreSimulationSanityCheck);
        });
    //----------------------------------------------------------------------
    // Define the time integration method.
    // Here we use dual time stepping with acoustic and advection steps.
    // The acoustic step is executed every physical time step, while the advection step is
    // executed at a lower frequency determined by the advection time step.
    // Note that only in acoustic steps the time integration is carried out.
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
                simulation_pipeline.run_hooks(SimulationHookPoint::ParticleSort);

                // Rigid solid bodies never move, so their cell-linked list stays
                // valid from initialization; only rebuild per step when moving
                // (composite/elastic) structures are present.
                if (!structure_configurations.empty())
                {
                    solid_cell_linked_list.exec();
                }

                for (ParticleDynamicsGroup *structure_configuration : structure_configurations)
                    structure_configuration->exec();

                fluid_configuration.exec();
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
void FluidSimulationBuilder::buildParticleDeletionIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, RealBody &real_body)
{
    auto &config_manager = sim.getConfigManager();
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    if (fluid_solver_config.particle_deletion_)
    {
        auto &particle_deletion = main_methods.template addStateDynamics<
            OutflowParticleDeletion>(real_body);

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleDeletion, [&]()
            { particle_deletion.exec(); });
    }
}
//=================================================================================================//
void FluidSimulationBuilder::buildParticleSortIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, RealBody &real_body)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();

    if (fluid_solver_config.particle_sorting_)
    {
        auto &particle_sort = main_methods.addSortDynamics(real_body);

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleSort, [&]()
            {
                if (time_stepper.getIterationStep() % fluid_solver_config.sort_frequency_ == 0)
                {
                    particle_sort.exec();
                } });
    }
}
//=================================================================================================//
BaseDynamics<void> &FluidSimulationBuilder::addTransportVelocityCorrection(
    MainMethods &main_methods, SPHBody &sph_body, FluidSolverConfig &fluid_solver_config)
{
    if (fluid_solver_config.surface_type_ == "confined")
    {
        return main_methods.template addStateDynamics<
            TransportVelocityCorrectionCK, TruncatedLinear>(sph_body);
    }

    if (fluid_solver_config.surface_type_ == "open_boundary")
    {
        return main_methods.template addStateDynamics<
            TransportVelocityCorrectionCK, TruncatedLinear, BulkParticles>(sph_body);
    }

    if (fluid_solver_config.surface_type_ == "free_stream")
    {
        return main_methods.template addStateDynamics<
            TransportVelocityCorrectionCK, NoLimiter, BulkParticles>(sph_body);
    }

    throw std::runtime_error(
        "FluidSimulationBuilder::addTransportVelocityCorrection: no supported flow type found!");
}
//=================================================================================================//
} // namespace SPH
