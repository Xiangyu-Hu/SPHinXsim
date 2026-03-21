#include "sph_simulation.h"

#include <fstream>
#include <stdexcept>

namespace SPH
{
//=================================================================================================//
SPHSimulation::SPHSimulation(const std::filesystem::path &config_path) : config_path_(config_path) {}
//=================================================================================================//
void SPHSimulation::defineSPHSystem(const json &config, Real particle_spacing, const Vecd &domain_dims, Real boundary_width)
{
    BoundingBoxd system_domain_bounds(-boundary_width * Vecd::Ones(),
                                      domain_dims + boundary_width * Vecd::Ones());
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

    entity_manager_.addEntity<FluidBody>(name, &fluid_body);
    fluid_body_names_.push_back(name);
    return fluid_body;
}
//=================================================================================================//
SolidBody &SPHSimulation::addWall(const json &config, const Vecd &domain_dims, Real boundary_width)
{
    if (!sph_system_ptr_)
    {
        throw std::runtime_error(
            "SPHSimulation::addWall: SPH system is not defined. "
            "Ensure domain and particle parameters are configured first.");
    }

    const std::string name = config.at("name").get<std::string>();
    const Vecd dims = config.contains("domain_dimensions")
                          ? jsonToVecd(config.at("domain_dimensions"))
                          : domain_dims;

    Vecd inner_halfsize = 0.5 * dims;
    Vecd outer_halfsize = inner_halfsize + boundary_width * Vecd::Ones();
    auto &wall_shape = sph_system_ptr_->addShape<ComplexShape>(name);
    wall_shape.add<GeometricShapeBox>(Transform(inner_halfsize), outer_halfsize);
    wall_shape.subtract<GeometricShapeBox>(Transform(inner_halfsize), inner_halfsize);

    auto &wall_body = sph_system_ptr_->addBody<SolidBody>(wall_shape, name);
    wall_body.defineMaterial<Solid>();
    wall_body.generateParticles<BaseParticles, Lattice>();

    entity_manager_.addEntity<SolidBody>(name, &wall_body);
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
    std::string name = config.at("name").get<std::string>();
    if (config.contains("positions"))
    {
        StdVec<Vecd> positions;
        for (const auto &p : config.at("positions"))
            positions.push_back(jsonToVecd(p));
        observers_.push_back({name, positions});
    }
    else if (config.contains("position"))
    {
        observers_.push_back({name, {jsonToVecd(config.at("position"))}});
    }
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
void SPHSimulation::loadFromJson(const json &config)
{
    // Extract simulation parameters from config
    Real particle_spacing = 0.0;
    int particle_boundary_buffer = 0;
    Vecd domain_dims = Vecd::Zero();
    if (config.contains("particle_spacing"))
        particle_spacing = config.at("particle_spacing").get<Real>();
    if (config.contains("particle_boundary_buffer"))
        particle_boundary_buffer = config.at("particle_boundary_buffer").get<int>();
    if (config.contains("domain"))
        domain_dims = jsonToVecd(config.at("domain").at("dimensions"));
    const Real boundary_width = particle_spacing * static_cast<Real>(particle_boundary_buffer);

    if (!domain_dims.isZero() && particle_spacing > 0 && particle_boundary_buffer > 0)
        defineSPHSystem(config, particle_spacing, domain_dims, boundary_width);
    if (config.contains("fluid_blocks"))
        for (const auto &fb : config.at("fluid_blocks"))
            addFluidBody(fb);
    if (config.contains("walls"))
        for (const auto &w : config.at("walls"))
            addWall(w, domain_dims, boundary_width);
    if (config.contains("gravity"))
        enableGravity(config.at("gravity"));
    if (config.contains("observers"))
        for (const auto &obs : config.at("observers"))
            addObserver(obs);
    if (config.contains("solver"))
        useSolver(config.at("solver"));
    if (config.contains("end_time"))
        end_time_ = config.at("end_time").get<Real>();
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
    loadFromJson(config);
}
//=================================================================================================//
void SPHSimulation::run(Real end_time)
{
    //----------------------------------------------------------------------
    // Validate configuration
    //----------------------------------------------------------------------
    if (fluid_body_names_.empty())
    {
        std::cerr << "SPHSimulation::run: no fluid body defined.\n";
        return;
    }
    if (wall_body_names_.empty())
    {
        std::cerr << "SPHSimulation::run: no wall defined.\n";
        return;
    }
    if (!sph_system_ptr_)
    {
        std::cerr << "SPHSimulation::run: SPH system is not defined. "
                     "Call loadConfig() or loadFromJson() first.\n";
        return;
    }
    SPHSystem &sph_system = getSPHSystem();

    const std::string &fluid_name = fluid_body_names_.front();
    FluidBody &water_block = entity_manager_.getEntityByName<FluidBody>(fluid_name);
    const std::string &wall_name = wall_body_names_.front();
    SolidBody &wall_boundary = entity_manager_.getEntityByName<SolidBody>(wall_name);

    //----------------------------------------------------------------------
    // Create observer bodies and contacts (kept alive for the entire run)
    //----------------------------------------------------------------------
    std::vector<std::unique_ptr<Contact<>>> observer_contacts;
    observer_contacts.reserve(observers_.size());
    for (const auto &obs : observers_)
    {
        ObserverBody &obs_body = sph_system.addBody<ObserverBody>(obs.name);
        obs_body.generateParticles<ObserverParticles>(obs.positions);
        observer_contacts.push_back(std::make_unique<Contact<>>(
            obs_body, StdVec<RealBody *>{&water_block}));
    }

    //----------------------------------------------------------------------
    // Define body relations
    //----------------------------------------------------------------------
    Inner<> water_block_inner(water_block);
    Contact<> water_wall_contact(water_block, {&wall_boundary});

    //----------------------------------------------------------------------
    // Build solver and particle method containers
    //----------------------------------------------------------------------
    SPHSolver sph_solver(sph_system);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);

    //----------------------------------------------------------------------
    // Cell linked list and relation dynamics
    //----------------------------------------------------------------------
    auto &water_cell_linked_list =
        main_methods.addCellLinkedListDynamics(water_block);
    auto &wall_cell_linked_list =
        main_methods.addCellLinkedListDynamics(wall_boundary);
    auto &water_block_update_complex_relation =
        main_methods.addRelationDynamics(water_block_inner, water_wall_contact);

    std::vector<BaseDynamics<void> *> observer_relation_dynamics;
    observer_relation_dynamics.reserve(observers_.size());
    for (auto &obs_contact : observer_contacts)
    {
        observer_relation_dynamics.push_back(
            &main_methods.addRelationDynamics(*obs_contact));
    }

    auto &particle_sort = main_methods.addSortDynamics(water_block);

    //----------------------------------------------------------------------
    // Physical dynamics
    //----------------------------------------------------------------------
    auto &wall_boundary_normal_direction =
        host_methods.addStateDynamics<NormalFromBodyShapeCK>(wall_boundary);
    auto &water_advection_step_setup =
        main_methods.addStateDynamics<fluid_dynamics::AdvectionStepSetup>(
            water_block);
    auto &water_update_particle_position =
        main_methods.addStateDynamics<fluid_dynamics::UpdateParticlePosition>(
            water_block);

    Gravity gravity_force(gravity_enabled_ ? gravity_ : Vecd::Zero());
    auto &constant_gravity =
        main_methods.addStateDynamics<GravityForceCK<Gravity>>(water_block,
                                                               gravity_force);

    auto &fluid_linear_correction_matrix =
        main_methods
            .addInteractionDynamics<LinearCorrectionMatrix, WithUpdate>(
                water_block_inner, 0.5)
            .addPostContactInteraction(water_wall_contact);

    auto &fluid_acoustic_step_1st_half =
        main_methods
            .addInteractionDynamics<fluid_dynamics::AcousticStep1stHalf, OneLevel,
                                    AcousticRiemannSolverCK, LinearCorrectionCK>(
                water_block_inner)
            .addPostContactInteraction<Wall, AcousticRiemannSolverCK,
                                       LinearCorrectionCK>(water_wall_contact);

    auto &fluid_acoustic_step_2nd_half =
        main_methods
            .addInteractionDynamics<fluid_dynamics::AcousticStep2ndHalf, OneLevel,
                                    AcousticRiemannSolverCK, LinearCorrectionCK>(
                water_block_inner)
            .addPostContactInteraction<Wall, AcousticRiemannSolverCK,
                                       LinearCorrectionCK>(water_wall_contact);

    auto &fluid_density_regularization =
        main_methods
            .addInteractionDynamics<fluid_dynamics::DensitySummationCK>(
                water_block_inner)
            .addPostContactInteraction(water_wall_contact)
            .addPostStateDynamics<fluid_dynamics::DensityRegularization,
                                  FreeSurface>(water_block);

    //----------------------------------------------------------------------
    // Time step estimators
    //----------------------------------------------------------------------
    Fluid &fluid_material = DynamicCast<Fluid>(this, water_block.getBaseMaterial());
    const Real U_ref =
        fluid_material.ReferenceSoundSpeed() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
    auto &fluid_advection_time_step =
        main_methods.addReduceDynamics<fluid_dynamics::AdvectionTimeStepCK>(
            water_block, U_ref);
    auto &fluid_acoustic_time_step =
        main_methods.addReduceDynamics<fluid_dynamics::AcousticTimeStepCK<>>(
            water_block);

    //----------------------------------------------------------------------
    // I/O
    //----------------------------------------------------------------------
    auto &body_state_recorder =
        main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);
    body_state_recorder.addToWrite<Vecd>(wall_boundary, "NormalDirection");
    body_state_recorder.addToWrite<Real>(water_block, "Density");

    //----------------------------------------------------------------------
    // Observer output (pressure at observation points)
    //----------------------------------------------------------------------
    std::vector<BaseIO *> observer_pressure_outputs;
    observer_pressure_outputs.reserve(observers_.size());
    for (auto &obs_contact : observer_contacts)
    {
        auto &recorder =
            main_methods.addObserveRecorder<Real>("Pressure", *obs_contact);
        observer_pressure_outputs.push_back(&recorder);
    }

    //----------------------------------------------------------------------
    // Define time stepper
    //----------------------------------------------------------------------
    TimeStepper &time_stepper = sph_solver.defineTimeStepper(end_time);

    //----------------------------------------------------------------------
    // Setup advection-step trigger
    //----------------------------------------------------------------------
    auto &advection_step =
        time_stepper.addTriggerByInterval(fluid_advection_time_step.exec());
    size_t advection_steps = 1;
    const int screening_interval = 100;
    const int observation_interval = screening_interval * 2;
    auto &state_recording_trigger = time_stepper.addTriggerByInterval(0.1);

    //----------------------------------------------------------------------
    // Initialise (must run host dynamics first, then device)
    //----------------------------------------------------------------------
    wall_boundary_normal_direction.exec();
    constant_gravity.exec();

    water_cell_linked_list.exec();
    wall_cell_linked_list.exec();
    water_block_update_complex_relation.exec();
    for (auto *rel : observer_relation_dynamics)
        rel->exec();

    fluid_density_regularization.exec();
    water_advection_step_setup.exec();
    fluid_linear_correction_matrix.exec();

    //----------------------------------------------------------------------
    // First output
    //----------------------------------------------------------------------
    body_state_recorder.writeToFile();
    for (auto *obs_out : observer_pressure_outputs)
        obs_out->writeToFile(advection_steps);

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
        Real acoustic_dt =
            time_stepper.incrementPhysicalTime(fluid_acoustic_time_step);
        fluid_acoustic_step_1st_half.exec(acoustic_dt);
        fluid_acoustic_step_2nd_half.exec(acoustic_dt);
        interval_acoustic_step += TickCount::now() - time_instance;

        // Slower advection stepping
        if (advection_step(fluid_advection_time_step))
        {
            advection_steps++;
            water_update_particle_position.exec();

            time_instance = TickCount::now();
            if (advection_steps % screening_interval == 0)
            {
                std::cout << std::fixed << std::setprecision(9)
                          << "N=" << advection_steps
                          << "  Time = " << time_stepper.getPhysicalTime()
                          << "  advection_dt = " << advection_step.getInterval()
                          << "  acoustic_dt = " << time_stepper.getGlobalTimeStepSize()
                          << "\n";
            }

            if (advection_steps % observation_interval == 0)
            {
                for (auto *rel : observer_relation_dynamics)
                    rel->exec();
                for (auto *obs_out : observer_pressure_outputs)
                    obs_out->writeToFile(advection_steps);
            }

            if (state_recording_trigger())
                body_state_recorder.writeToFile();

            interval_output += TickCount::now() - time_instance;

            // Update configuration
            time_instance = TickCount::now();
            if (advection_steps % 100)
                particle_sort.exec();
            water_cell_linked_list.exec();
            water_block_update_complex_relation.exec();
            interval_updating_configuration += TickCount::now() - time_instance;

            // Update dynamics for next advection step
            time_instance = TickCount::now();
            fluid_density_regularization.exec();
            water_advection_step_setup.exec();
            fluid_linear_correction_matrix.exec();
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
