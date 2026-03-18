#include "sph_simulation.h"

#include "sphinxsys.h"

#include <fstream>
#include <stdexcept>

namespace SPH
{
//=================================================================================================//
SPHSimulation::SPHSimulation(const std::string &config_path) : config_path_(config_path) {}
//=================================================================================================//
void SPHSimulation::createDomain(VecdRef domain_dimensions,
                                 Real particle_spacing)
{
    defineDomain(domain_dimensions, particle_spacing);
}
//=================================================================================================//
void SPHSimulation::defineDomain(VecdRef domain_dimensions,
                                 Real particle_spacing)
{
    domain_dims_ = domain_dimensions;
    dp_ref_ = particle_spacing;
}
//=================================================================================================//
FluidBlockBuilder &SPHSimulation::addFluidBlock(const std::string &name)
{
    fluid_blocks_.push_back(std::make_unique<FluidBlockBuilder>(name));
    return *fluid_blocks_.back();
}
//=================================================================================================//
WallBuilder &SPHSimulation::addWall(const std::string &name)
{
    walls_.push_back(std::make_unique<WallBuilder>(name));
    return *walls_.back();
}
//=================================================================================================//
void SPHSimulation::enableGravity(VecdRef gravity)
{
    gravity_ = gravity;
    gravity_enabled_ = true;
}
//=================================================================================================//
void SPHSimulation::addObserver(const std::string &name, VecdRef position)
{
    observers_.push_back({name, {position}});
}
//=================================================================================================//
void SPHSimulation::addObserver(const std::string &name,
                                const StdVec<Vecd> &positions)
{
    observers_.push_back({name, positions});
}
//=================================================================================================//
SolverConfig &SPHSimulation::useSolver()
{
    if (!solver_config_)
        solver_config_ = std::make_unique<SolverConfig>();
    return *solver_config_;
}
//=================================================================================================//
void SPHSimulation::loadFromJson(const json &config)
{
    // Domain
    if (config.contains("domain"))
    {
        const auto &dom = config["domain"];
        defineDomain(jsonToVecd(dom["dimensions"]),
                     dom["particle_spacing"].get<Real>());
    }

    // Fluid blocks
    if (config.contains("fluid_blocks"))
    {
        for (const auto &fb : config["fluid_blocks"])
        {
            auto &builder = addFluidBlock(fb["name"].get<std::string>());
            if (fb.contains("dimensions"))
                builder.block(jsonToVecd(fb["dimensions"]));
            if (fb.contains("density") && fb.contains("sound_speed"))
                builder.material(fb["density"].get<Real>(),
                                 fb["sound_speed"].get<Real>());
        }
    }

    // Walls — "domain_dimensions" defaults to the simulation domain
    if (config.contains("walls"))
    {
        for (const auto &w : config["walls"])
        {
            auto &builder = addWall(w["name"].get<std::string>());
            Vecd dims = w.contains("domain_dimensions")
                            ? jsonToVecd(w["domain_dimensions"])
                            : domain_dims_;
            if (w.contains("wall_width"))
                builder.hollowBox(dims, w["wall_width"].get<Real>());
        }
    }

    // Gravity
    if (config.contains("gravity"))
        enableGravity(jsonToVecd(config["gravity"]));

    // Observers
    if (config.contains("observers"))
    {
        for (const auto &obs : config["observers"])
        {
            std::string name = obs["name"].get<std::string>();
            if (obs.contains("positions"))
            {
                StdVec<Vecd> positions;
                for (const auto &p : obs["positions"])
                    positions.push_back(jsonToVecd(p));
                addObserver(name, positions);
            }
            else if (obs.contains("position"))
            {
                addObserver(name, jsonToVecd(obs["position"]));
            }
        }
    }

    // Solver
    if (config.contains("solver"))
    {
        const auto &solver = config["solver"];
        auto &sc = useSolver();
        if (solver.value("dual_time_stepping", false))
            sc.dualTimeStepping();
        if (solver.value("free_surface_correction", false))
            sc.freeSurfaceCorrection();
    }

    // End time (stored for the no-arg run() overload)
    if (config.contains("end_time"))
        end_time_ = config["end_time"].get<Real>();
}
//=================================================================================================//
void SPHSimulation::loadFromFile(const std::string &filepath)
{
    std::ifstream file(filepath);
    if (!file.is_open())
        throw std::runtime_error("SPHSimulation::loadFromFile: cannot open \"" +
                                 filepath + "\"");
    json config;
    file >> config;
    loadFromJson(config);
}
//=================================================================================================//
void SPHSimulation::run(Real end_time)
{
    loadFromFile(config_path_);
    //----------------------------------------------------------------------
    // Validate configuration
    //----------------------------------------------------------------------
    if (fluid_blocks_.empty())
    {
        std::cerr << "SPHSimulation::run: no fluid block defined.\n";
        return;
    }
    if (walls_.empty())
    {
        std::cerr << "SPHSimulation::run: no wall defined.\n";
        return;
    }
    if (dp_ref_ <= 0.0)
    {
        std::cerr << "SPHSimulation::run: domain is not defined. Call "
                     "defineDomain() or createDomain() first.\n";
        return;
    }

    //----------------------------------------------------------------------
    // Derive geometry parameters
    //----------------------------------------------------------------------
    const FluidBlockBuilder &fluid_cfg = *fluid_blocks_[0];
    const WallBuilder &wall_cfg = *walls_[0];
    const Real BW = wall_cfg.getWallWidth();

    //----------------------------------------------------------------------
    // Build the SPH system
    //----------------------------------------------------------------------
    BoundingBoxd system_domain_bounds(-BW * Vecd::Ones(),
                                      domain_dims_ + BW * Vecd::Ones());
    sph_system_ = std::make_unique<SPHSystem>(system_domain_bounds, dp_ref_);
    SPHSystem &sph_system = *sph_system_;

    //----------------------------------------------------------------------
    // Create fluid body (rectangular block starting at the coordinate origin)
    //----------------------------------------------------------------------
    Vecd water_halfsize = 0.5 * fluid_cfg.getDimensions();
    GeometricShapeBox initial_water_block(Transform(water_halfsize),
                                          water_halfsize, fluid_cfg.getName());
    FluidBody water_block(sph_system, initial_water_block);
    water_block.defineMaterial<WeaklyCompressibleFluid>(fluid_cfg.getRho0(),
                                                        fluid_cfg.getC());
    water_block.generateParticles<BaseParticles, Lattice>();

    //----------------------------------------------------------------------
    // Create wall body (hollow box aligned with the domain origin)
    //----------------------------------------------------------------------
    Vecd inner_halfsize = 0.5 * wall_cfg.getDomainDimensions();
    Vecd outer_halfsize = inner_halfsize + BW * Vecd::Ones();
    // Both inner and outer box are centered at inner_halfsize
    ComplexShape wall_complex_shape(wall_cfg.getName());
    wall_complex_shape.add<GeometricShapeBox>(Transform(inner_halfsize),
                                              outer_halfsize);
    wall_complex_shape.subtract<GeometricShapeBox>(Transform(inner_halfsize),
                                                   inner_halfsize);
    SolidBody wall_boundary(sph_system, wall_complex_shape);
    wall_boundary.defineMaterial<Solid>();
    wall_boundary.generateParticles<BaseParticles, Lattice>();

    //----------------------------------------------------------------------
    // Create observer bodies and contacts (kept alive for the entire run)
    //----------------------------------------------------------------------
    std::vector<std::unique_ptr<Contact<>>> observer_contacts;
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
    const Real U_ref =
        fluid_cfg.getC() / 10.0; // c_f = 10 * U_ref => U_ref = c_f / 10
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
