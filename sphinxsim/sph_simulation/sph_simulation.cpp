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
void SPHSimulation::defineSPHSystem(const json &config)
{
    Real particle_spacing = config.at("particle_spacing").get<Real>();
    Vecd domain_dims = jsonToVecd(config.at("domain").at("dimensions"));
    int particle_boundary_buffer = config.at("particle_boundary_buffer").get<int>();
    Real boundary_width = particle_boundary_buffer * particle_spacing;
    BoundingBoxd system_domain_bounds(
        Vecd::Constant(-boundary_width), domain_dims + Vecd::Constant(boundary_width));
    sph_system_ptr_ = std::make_unique<SPHSystem>(system_domain_bounds, particle_spacing);
}
//=================================================================================================//
FluidBody &SPHSimulation::addFluidBody(const json &config)
{
    if (!sph_system_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::addFluidBody: SPH system is not defined. "
            "Ensure domain and particle parameters are configured first.");
    }

    const std::string name = config.at("name").get<std::string>();
    const Vecd dimensions = jsonToVecd(config.at("dimensions"));
    const Real rho0 = config.value("density", Real(1.0));
    const Real c = config.value("sound_speed", Real(10.0));

    Vecd fluid_halfsize = 0.5 * dimensions;
    auto &fluid_shape = sph_system_ptr_->addShape<GeometricShapeBox>(
        Transform(fluid_halfsize), fluid_halfsize, name);

    auto &fluid_body = sph_system_ptr_->addBody<FluidBody>(fluid_shape, name);
    fluid_body.defineMaterial<WeaklyCompressibleFluid>(rho0, c);
    fluid_body.generateParticles<BaseParticles, Lattice>();

    entity_manager_.addEntity(name, &fluid_body);
    fluid_body_names_.push_back(name);
    return fluid_body;
}
//=================================================================================================//
SolidBody &SPHSimulation::addWall(const json &config)
{
    if (!sph_system_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::addWall: SPH system is not defined. "
            "Ensure domain and particle parameters are configured first.");
    }

    const std::string name = config.at("name").get<std::string>();
    const Vecd dims = jsonToVecd(config.at("dimensions"));
    const Real boundary_width = config.at("boundary_width").get<Real>();

    Vecd inner_halfsize = 0.5 * dims;
    Vecd outer_halfsize = inner_halfsize + Vecd::Constant(boundary_width);
    auto &wall_shape = sph_system_ptr_->addShape<ComplexShape>(name);
    wall_shape.add<GeometricShapeBox>(Transform(inner_halfsize), outer_halfsize);
    wall_shape.subtract<GeometricShapeBox>(Transform(inner_halfsize), inner_halfsize);

    auto &wall_body = sph_system_ptr_->addBody<SolidBody>(wall_shape, name);
    wall_body.defineMaterial<Solid>();
    wall_body.generateParticles<BaseParticles, Lattice>();

    entity_manager_.addEntity(name, &wall_body);
    wall_body_names_.push_back(name);
    return wall_body;
}
//=================================================================================================//
void SPHSimulation::enableGravity(const json &config)
{
    gravity_ = jsonToVecd(config);
    gravity_enabled_ = true;
}
//=================================================================================================//
void SPHSimulation::addObserver(const json &config)
{
    if (!sph_system_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::addObserver: SPH system is not defined. "
            "Ensure domain and particle parameters are configured first.");
    }

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
    else
    {
        throw std::runtime_error(
            "SPHSimulation::addObserver: observer must define either 'position' or 'positions'.");
    }

    ObserverBody &observer_body = sph_system_ptr_->addBody<ObserverBody>(name);
    observer_body.generateParticles<ObserverParticles>(positions);

    entity_manager_.addEntity(name, &observer_body);
    observer_body_names_.push_back(name);
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
void SPHSimulation::buildExecutableState()
{
    if (!sph_system_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::buildExecutableState: SPH system is not defined.");
    }
    if (fluid_body_names_.empty())
    {
        throw std::runtime_error(
            "SPHSimulation::buildExecutableState: no fluid body defined.");
    }
    if (wall_body_names_.empty())
    {
        throw std::runtime_error(
            "SPHSimulation::buildExecutableState: no wall defined.");
    }

    SPHSystem &sph_system = getSPHSystem();
    auto &fluid_body = entity_manager_.getEntityByName<FluidBody>(fluid_body_names_.front());
    StdVec<SolidBody *> solid_bodies = entity_manager_.entitiesWith<SolidBody>();
    auto &fluid_observer = entity_manager_.getEntityByName<ObserverBody>("FluidObserver");

    auto &fluid_inner = sph_system.addInnerRelation(fluid_body);
    auto &fluid_wall_contact = sph_system.addContactRelation(fluid_body, solid_bodies);
    auto &fluid_observer_contact = sph_system.addContactRelation(fluid_observer, fluid_body);

    sph_solver_ = std::make_unique<SPHSolver>(sph_system);
    auto &main_methods = sph_solver_->addParticleMethodContainer(par_ck);
    auto &host_methods = sph_solver_->addParticleMethodContainer(par_host);

    solid_normal_direction_ = &host_methods.addStateDynamics<NormalFromBodyShapeCK>(solid_bodies);

    solid_cell_linked_list_ = &main_methods.addCellLinkedListDynamics(solid_bodies);
    fluid_update_configuration_ = &main_methods.addParticleDynamicsGroup()
                                       .add(&main_methods.addCellLinkedListDynamics(fluid_body))
                                       .add(&main_methods.addRelationDynamics(fluid_inner, fluid_wall_contact));
    observer_update_configuration_ = &main_methods.addRelationDynamics(fluid_observer_contact);
    particle_sort_ = &main_methods.addSortDynamics(fluid_body);

    Gravity gravity_force(gravity_enabled_ ? gravity_ : Vecd::Zero());
    constant_gravity_ = &main_methods.addStateDynamics<GravityForceCK<Gravity>>(fluid_body, gravity_force);
    fluid_advection_step_setup_ = &main_methods.addStateDynamics<fluid_dynamics::AdvectionStepSetup>(fluid_body);
    fluid_update_particle_position_ = &main_methods.addStateDynamics<fluid_dynamics::UpdateParticlePosition>(fluid_body);

    fluid_linear_correction_matrix_ =
        &main_methods.addInteractionDynamics<LinearCorrectionMatrix, WithUpdate>(fluid_inner, 0.5)
             .addPostContactInteraction(fluid_wall_contact);

    fluid_acoustic_step_1st_half_ =
        &main_methods.addInteractionDynamics<fluid_dynamics::AcousticStep1stHalf, OneLevel,
                                             AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_inner)
             .addPostContactInteraction<Wall, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_wall_contact);

    fluid_acoustic_step_2nd_half_ =
        &main_methods.addInteractionDynamics<fluid_dynamics::AcousticStep2ndHalf, OneLevel,
                                             AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_inner)
             .addPostContactInteraction<Wall, AcousticRiemannSolverCK, LinearCorrectionCK>(fluid_wall_contact);

    fluid_density_regularization_ =
        &main_methods.addInteractionDynamics<fluid_dynamics::DensitySummationCK>(fluid_inner)
             .addPostContactInteraction(fluid_wall_contact)
             .addPostStateDynamics<fluid_dynamics::DensityRegularization, FreeSurface>(fluid_body);

    Fluid &fluid_material = DynamicCast<Fluid>(this, fluid_body.getBaseMaterial());
    const Real U_ref = fluid_material.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    fluid_advection_time_step_ = &main_methods.addReduceDynamics<fluid_dynamics::AdvectionTimeStepCK>(fluid_body, U_ref);
    fluid_acoustic_time_step_ = &main_methods.addReduceDynamics<fluid_dynamics::AcousticTimeStepCK<>>(fluid_body);

    body_state_recorder_ = &main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);
    for (auto &solid_body : solid_bodies)
    {
        body_state_recorder_->addToWrite<Vecd>(*solid_body, "NormalDirection");
    }
    body_state_recorder_->addToWrite<Real>(fluid_body, "Density");

    observer_pressure_output_ = &main_methods.addObserveRecorder<Real>("Pressure", fluid_observer_contact);
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

    solid_normal_direction_->exec();
    constant_gravity_->exec();

    solid_cell_linked_list_->exec();
    fluid_update_configuration_->exec();

    fluid_density_regularization_->exec();
    fluid_advection_step_setup_->exec();
    fluid_linear_correction_matrix_->exec();

    body_state_recorder_->writeToFile();

    observer_update_configuration_->exec();
    observer_pressure_output_->writeToFile(advection_steps_);

    executable_state_ready_ = true;
}
//=================================================================================================//
void SPHSimulation::buildSimulationFromJson(const json &config)
{
    defineSPHSystem(config);
    if (config.contains("fluid_blocks"))
        for (const auto &fb : config.at("fluid_blocks"))
            addFluidBody(fb);
    if (config.contains("walls"))
        for (const auto &w : config.at("walls"))
            addWall(w);
    if (config.contains("gravity"))
        enableGravity(config.at("gravity"));
    if (config.contains("observers"))
        for (const auto &obs : config.at("observers"))
            addObserver(obs);
    if (config.contains("solver"))
        useSolver(config.at("solver"));
    if (config.contains("end_time"))
        end_time_ = config.at("end_time").get<Real>();

    buildExecutableState();
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
void SPHSimulation::run(Real end_time)
{
    if (!executable_state_ready_ || !sph_solver_)
    {
        std::cerr << "SPHSimulation::run: executable state is not ready. "
                     "Call initializeSimulation() after build.\n";
        return;
    }

    TimeStepper &time_stepper = sph_solver_->defineTimeStepper(end_time);

    //----------------------------------------------------------------------
    // Setup advection-step trigger
    //----------------------------------------------------------------------
    auto &advection_step = time_stepper.addTriggerByInterval(fluid_advection_time_step_->exec());
    const int screening_interval = 100;
    const int observation_interval = screening_interval * 2;
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(0.1);

    //----------------------------------------------------------------------
    // Timing
    //----------------------------------------------------------------------
    TimeInterval interval_output;
    TimeInterval interval_advection_step;
    TimeInterval interval_acoustic_step;
    TimeInterval interval_updating_configuration;

    //----------------------------------------------------------------------
    // Time integration loop
    //----------------------------------------------------------------------
    TickCount t0 = TickCount::now();
    while (!time_stepper.isEndTime())
    {
        // Fast acoustic sub-stepping
        TickCount time_instance = TickCount::now();
        Real acoustic_dt = time_stepper.incrementPhysicalTime(*fluid_acoustic_time_step_);
        fluid_acoustic_step_1st_half_->exec(acoustic_dt);
        fluid_acoustic_step_2nd_half_->exec(acoustic_dt);
        interval_acoustic_step += TickCount::now() - time_instance;

        // Slower advection stepping
        if (advection_step(*fluid_advection_time_step_))
        {
            advection_steps_++;
            fluid_update_particle_position_->exec();

            time_instance = TickCount::now();
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
                observer_update_configuration_->exec();
                observer_pressure_output_->writeToFile(advection_steps_);
            }

            if (state_recording_trigger())
                body_state_recorder_->writeToFile();

            interval_output += TickCount::now() - time_instance;

            // Update configuration
            time_instance = TickCount::now();
            if (advection_steps_ % 100 == 0)
            {
                particle_sort_->exec();
            }
            fluid_update_configuration_->exec();
            interval_updating_configuration += TickCount::now() - time_instance;

            // Update dynamics for next advection step
            time_instance = TickCount::now();
            fluid_density_regularization_->exec();
            fluid_advection_step_setup_->exec();
            fluid_linear_correction_matrix_->exec();
            interval_advection_step += TickCount::now() - time_instance;
        }
    }

    //----------------------------------------------------------------------
    // Summary
    //----------------------------------------------------------------------
    TimeInterval tt = TickCount::now() - t0 - interval_output;
    std::cout << "Total wall time for computation: " << tt.seconds()
              << " seconds.\n";
    std::cout << std::fixed << std::setprecision(9)
              << "interval_advection_step = " << interval_advection_step.seconds()
              << "\n"
              << "interval_acoustic_step = " << interval_acoustic_step.seconds()
              << "\n"
              << "interval_updating_configuration = "
              << interval_updating_configuration.seconds() << "\n";
}
//=================================================================================================//
} // namespace SPH
