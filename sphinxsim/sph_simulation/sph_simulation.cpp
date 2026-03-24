#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
SPHSimulation::SPHSimulation(const fs::path &config_path) : config_path_(config_path) {}
//=================================================================================================//
void SPHSimulation::resetOutputRoot(const fs::path &output_root)
{
    if (!fs::exists(output_root))
    {
        fs::create_directory(output_root);
    }
    IOEnvironment &io_env = sph_system_ptr_->getIOEnvironment();
    io_env.resetOutputFolder((output_root / "output").string());
    io_env.resetRestartFolder((output_root / "restart").string());
    io_env.resetReloadFolder((output_root / "reload").string());
}
//=================================================================================================//
SPHSystem &SPHSimulation::defineSPHSystem(const json &config)
{
    Real particle_spacing = config.at("particle_spacing").get<Real>();
    Vecd domain_dims = jsonToVecd(config.at("domain").at("dimensions"));
    int particle_boundary_buffer = config.at("particle_boundary_buffer").get<int>();
    Real boundary_width = particle_boundary_buffer * particle_spacing;
    BoundingBoxd system_domain_bounds(
        Vecd::Constant(-boundary_width), domain_dims + Vecd::Constant(boundary_width));
    sph_system_ptr_ = std::make_unique<SPHSystem>(system_domain_bounds, particle_spacing);
    return *sph_system_ptr_;
}
//=================================================================================================//
FluidBody &SPHSimulation::addFluidBody(SPHSystem &sph_system, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    const Vecd dimensions = jsonToVecd(config.at("dimensions"));
    const Real rho0 = config.value("density", Real(1.0));
    const Real c = config.value("sound_speed", Real(10.0));

    Vecd fluid_halfsize = 0.5 * dimensions;
    auto &fluid_shape = sph_system.addShape<GeometricShapeBox>(
        Transform(fluid_halfsize), fluid_halfsize, name);

    auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
    fluid_body.defineMaterial<WeaklyCompressibleFluid>(rho0, c);
    fluid_body.generateParticles<BaseParticles, Lattice>();

    entity_manager_.addEntity(name, &fluid_body);
    return fluid_body;
}
//=================================================================================================//
SolidBody &SPHSimulation::addWall(SPHSystem &sph_system, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    const Vecd dims = jsonToVecd(config.at("dimensions"));
    const Real boundary_width = config.at("boundary_width").get<Real>();

    Vecd inner_halfsize = 0.5 * dims;
    Vecd outer_halfsize = inner_halfsize + Vecd::Constant(boundary_width);
    auto &wall_shape = sph_system.addShape<ComplexShape>(name);
    wall_shape.add<GeometricShapeBox>(Transform(inner_halfsize), outer_halfsize);
    wall_shape.subtract<GeometricShapeBox>(Transform(inner_halfsize), inner_halfsize);

    auto &wall_body = sph_system.addBody<SolidBody>(wall_shape, name);
    wall_body.defineMaterial<Solid>();
    wall_body.generateParticles<BaseParticles, Lattice>();

    entity_manager_.addEntity(name, &wall_body);
    return wall_body;
}
//=================================================================================================//
ObserverBody &SPHSimulation::addObserver(SPHSystem &sph_system, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    StdVec<Vecd> positions;
    if (config.contains("positions"))
    {
        for (const auto &p : config.at("positions"))
            positions.push_back(jsonToVecd(p));
    }
    else if (config.contains("position"))
    {
        positions.push_back(jsonToVecd(config.at("position")));
    }

    ObserverBody &observer_body = sph_system.addBody<ObserverBody>(name);
    observer_body.generateParticles<ObserverParticles>(positions);

    entity_manager_.addEntity(name, &observer_body);
    return observer_body;
}
//=================================================================================================//
SolverConfig &SPHSimulation::useSolver(const json &config)
{
    if (!solver_config_)
        solver_config_ = std::make_unique<SolverConfig>();
    auto &sc = *solver_config_;
    if (config.value("dual_time_stepping", false))
        sc.dualTimeStepping();
    if (config.value("free_surface_correction", false))
        sc.freeSurfaceCorrection();
    return sc;
}
//=================================================================================================//
void SPHSimulation::buildSimulationFromJson(const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = defineSPHSystem(config);
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
    if (config.contains("fluid_blocks"))
        for (const auto &fb : config.at("fluid_blocks"))
            addFluidBody(sph_system, fb);
    if (config.contains("walls"))
        for (const auto &w : config.at("walls"))
            addWall(sph_system, w);
    if (config.contains("observers"))
        for (const auto &obs : config.at("observers"))
            addObserver(sph_system, obs);
    if (config.contains("solver"))
        useSolver(config.at("solver"));
    if (config.contains("end_time"))
        end_time_ = config.at("end_time").get<Real>();
    //----------------------------------------------------------------------
    //	Define body relation map.
    //	The relations give the topological connections within a body
    //  or with other bodies within interaction range.
    //  Generally, we first define all the inner relations, then the contact relations.
    //----------------------------------------------------------------------
    auto &fluid_body = *entity_manager_.entitiesWith<FluidBody>().front(); // assume only one fluid body for now
    StdVec<SolidBody *> solid_bodies = entity_manager_.entitiesWith<SolidBody>();
    auto &fluid_observer = entity_manager_.getEntityByName<ObserverBody>("FluidObserver");

    auto &fluid_inner = sph_system.addInnerRelation(fluid_body);
    auto &fluid_wall_contact = sph_system.addContactRelation(fluid_body, solid_bodies);
    auto &fluid_observer_contact = sph_system.addContactRelation(fluid_observer, fluid_body);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    sph_solver_ = std::make_unique<SPHSolver>(sph_system);
    auto &main_methods = sph_solver_->addParticleMethodContainer(par_ck);
    auto &host_methods = sph_solver_->addParticleMethodContainer(par_host);
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
    auto &particle_sort = main_methods.addSortDynamics(fluid_body);

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
    auto &fluid_advection_time_step = main_methods.addReduceDynamics<fluid_dynamics::AdvectionTimeStepCK>(fluid_body, U_ref);
    auto &fluid_acoustic_time_step = main_methods.addReduceDynamics<fluid_dynamics::AcousticTimeStepCK<>>(fluid_body);
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
    initialization_pipeline_.main_steps.push_back(
        [&]()
        {
            solid_normal_direction.exec();
            initialization_pipeline_.run_hooks(InitializationHookPoint::InitalConditions);

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
    auto &time_stepper = sph_solver_->getTimeStepper();
    auto &advection_step = time_stepper.addTriggerByInterval(fluid_advection_time_step.exec());
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(0.1);
    int screening_interval = 100;
    int observation_interval = screening_interval * 2;
    //----------------------------------------------------------------------
    //	Define time-integration method.
    //  Here we use dual time stepping with acoustic and advection steps.
    //  The acoustic step is executed every physical time step, while the advection step is
    //  executed at a lower frequency determined by the advection time step.
    //----------------------------------------------------------------------
    simulation_pipeline_.main_steps.push_back( // acoustic step
        [&]()
        {
            Real dt = time_stepper.incrementPhysicalTime(fluid_acoustic_time_step);
            fluid_acoustic_step_1st_half.exec(dt);
            fluid_acoustic_step_2nd_half.exec(dt);
        });

    simulation_pipeline_.main_steps.push_back( // advection step
        [&, screening_interval, observation_interval]()
        {
            if (advection_step(fluid_advection_time_step))
            {
                advection_steps_++;
                fluid_update_particle_position.exec();
                fluid_density_regularization.exec();
                fluid_advection_step_setup.exec();
                fluid_linear_correction_matrix.exec();

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

                fluid_update_configuration.exec();
                fluid_density_regularization.exec();
                fluid_advection_step_setup.exec();
                fluid_linear_correction_matrix.exec();
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
        initialization_pipeline_.insert_hook(
            InitializationHookPoint::InitalConditions, [&]()
            { constant_gravity.exec(); });
    }
}
//=================================================================================================//
void SPHSimulation::loadConfig()
{
    std::ifstream file(config_path_);
    if (!file.is_open())
    {
        throw std::runtime_error(
            "SPHSimulation::loadConfig: unable to open config file " + config_path_.string());
    }
    json config;
    file >> config;
    buildSimulationFromJson(config);
}
//=================================================================================================//
void SPHSimulation::initializeSimulation()
{
    if (!sph_solver_)
    {
        throw std::runtime_error(
            "SPHSimulation::initializeSimulation: simulation is not built. "
            "Call loadConfig() or buildSimulationFromJson() first.");
    }

    for (auto &step : initialization_pipeline_.main_steps)
    {
        step(); // each step touches all cells internally
    }

    executable_state_ready_ = true;
}
//=================================================================================================//
void SPHSimulation::run(Real end_time)
{
    if (!executable_state_ready_)
    {
        std::cerr << "SPHSimulation::run: Simulation is not initialized. "
                     "Call initializeSimulation() before run.\n";
        return;
    }

    while (!sph_solver_->getTimeStepper().isEndTime(end_time))
    {
        for (auto &step : simulation_pipeline_.main_steps)
        {
            step(); // each step touches all cells internally
        }
    }
}
//=================================================================================================//
} // namespace SPH
