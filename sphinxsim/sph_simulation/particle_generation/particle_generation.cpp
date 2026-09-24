#include "particle_generation.hpp"

#include "geometry_builder.h"
#include "recording_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
ParticleGeneration::ParticleGeneration() {}
//=================================================================================================//
ParticleGeneration::~ParticleGeneration() = default;
//=================================================================================================//
void ParticleGeneration::buildParticleGeneration(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    EntityManager &config_manager = sim.getConfigManager();
    RelaxationSystem &relaxation_system = defineRelaxationSystem(config_manager, config);
    //----------------------------------------------------------------------
    addAllBodies(relaxation_system, config_manager, config.at("bodies"));
    defineBodyRelations(relaxation_system);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = defineSPHSolver(relaxation_system, config);
    auto &host_methods = sph_solver.getHostMethodContainer();
    auto &randomize_particle_position = randomizeParticlePositions(relaxation_system, host_methods);

    auto &main_methods = sph_solver.getMainMethodContainer();
    RecordingBuilder::createBodyStatesRecording(relaxation_system, config_manager, main_methods);
    auto &body_update_configuration = addConfigurationDynamics(relaxation_system, main_methods);
    auto &relaxation_residual = addRelaxationResidue(relaxation_system, config_manager, main_methods);
    auto &relaxation_scaling = addRelaxationScaling(relaxation_system, config_manager, main_methods);
    auto &update_particle_position = addRelaxationPositionUpdate(relaxation_system, config_manager, main_methods);
    auto &dummy_cell_linked_list = addDummyBodiesCellLinkedListDynamics(relaxation_system, main_methods);
    //----------------------------------------------------------------------
    //	Define simple file input and outputs functions.
    //----------------------------------------------------------------------
    RecordingBuilder::finalizeBodyStatesRecording(relaxation_system, config_manager, config);
    auto &write_particle_reload_files = main_methods.addIODynamics<ReloadParticleIOCK>(relaxation_system);
    auto &body_state_recorder = RecordingBuilder::getBodyStatesRecording(config_manager);
    //----------------------------------------------------------------------
    //	Out initial particle distribution after setting up.
    //----------------------------------------------------------------------
    body_state_recorder.writeToFile(0);
    //----------------------------------------------------------------------
    //	Define particle relaxation simulation.
    //----------------------------------------------------------------------
    relaxation_pipeline_.main_steps.push_back(
        [&]()
        {
            randomize_particle_position.exec();
            relaxation_pipeline_.run_hooks(RelaxationHookPoint::Initialization);
            dummy_cell_linked_list.exec();

            UnsignedInt ite_p = 0;
            while (ite_p < relaxation_parameters_.total_iterations)
            {
                body_update_configuration.exec();

                relaxation_residual.exec();
                Real relaxation_step = relaxation_scaling.exec();
                relaxation_pipeline_.run_hooks(RelaxationHookPoint::Constraints);
                update_particle_position.exec(relaxation_step);

                ite_p += 1;
                if (ite_p % 100 == 0)
                {
                    std::cout << std::fixed << std::setprecision(9) << "Relaxation steps N = " << ite_p << "\n";
                    body_state_recorder.writeToFile(ite_p);
                }
            }

            std::cout << "\n---------------------------------------" << std::endl;
            std::cout << "The physics relaxation process finish !" << std::endl;
            std::cout << "---------------------------------------" << std::endl;
        });

    //----------------------------------------------------------------------
    // Define optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    addRelaxationConstraintsIfPresent(relaxation_system, config_manager, main_methods, config);
    //----------------------------------------------------------------------
    //	Run on CPU after relaxation finished and output results.
    //----------------------------------------------------------------------
    auto &body_normal_direction = addBodyNormalDirection(
        relaxation_system, config_manager, host_methods);
    //----------------------------------------------------------------------
    // Define after relaxation steps using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    reload_io_pipeline_.main_steps.push_back(
        [&]()
        {
            body_normal_direction.exec();
            write_particle_reload_files.writeToFile();

            std::cout << "\n---------------------------------------" << std::endl;
            std::cout << "The particle reload files ready !" << std::endl;
            std::cout << "---------------------------------------" << std::endl;
        });
}
//=================================================================================================//
void ParticleGeneration::runRelaxation()
{
    if (!bodies_config_.relaxation_bodies_.empty())
    {
        for (auto &step : relaxation_pipeline_.main_steps)
        {
            step();
        }
    }

    for (auto &step : reload_io_pipeline_.main_steps)
    {
        step();
    }
}
//=================================================================================================//
RelaxationSystem &ParticleGeneration::defineRelaxationSystem(
    EntityManager &config_manager, const json &config)
{
    auto &system_config = config_manager.getEntity<SystemDomainConfig>("SystemDomainConfig");
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    relaxation_system_ptr_ = std::make_unique<RelaxationSystem>(
        system_config.system_bounds_, system_config.particle_spacing_);
    relaxation_system_ptr_->writeSystemDomainShapeToVtp(scaling_config.getScalingRef("Length"));
    return *relaxation_system_ptr_.get();
}
//=================================================================================================//
SPHSolver &ParticleGeneration::defineSPHSolver(RelaxationSystem &relaxation_system, const json &config)
{
    if (config.contains("relaxation_parameters"))
    {
        relaxation_parameters_ = parseRelaxationParameters(config.at("relaxation_parameters"));
    }
    sph_solver_ptr_ = std::make_unique<SPHSolver>(relaxation_system);
    return *sph_solver_ptr_.get();
}
//=================================================================================================//
RelaxationParameters ParticleGeneration::parseRelaxationParameters(const json &config)
{
    RelaxationParameters parameters;
    if (config.contains("total_iterations"))
        parameters.total_iterations = config.at("total_iterations").get<UnsignedInt>();
    return parameters;
}
//=================================================================================================//
void ParticleGeneration ::addAllBodies(
    RelaxationSystem &relaxation_system, EntityManager &config_manager, const json &config)
{
    for (const auto &bd : config)
    {
        std::string body_name = bd.at("name").get<std::string>();

        GenerationBodyConfig generation_body_config;
        generation_body_config.name_ = body_name;
        Shape &shape = config_manager.getEntity<Shape>(body_name);
        auto &real_body = relaxation_system.addBody<RealBody>(shape, body_name);

        if (bd.contains("relaxation"))
        {
            generation_body_config.is_relaxation_body_ = true;
            RelaxationBodyConfig relax_body_config = parseRelaxationBodyConfig(body_name, bd.at("relaxation"));
            if (relax_body_config.with_level_set_)
            {
                LevelSetShape &level_set_shape =
                    real_body.defineBodyLevelSetShape(par_ck, 2.0).writeLevelSet();
                config_manager.addEntity(body_name, &level_set_shape);
            }
            bodies_config_.relaxation_bodies_.push_back(relax_body_config);
        }

        if (bd.contains("solid_body"))
        {
            generation_body_config.is_solid_body_ = true;
        }
        bodies_config_.all_bodies_.push_back(generation_body_config);

        StdVec<OrientedBox *> &blockers = *config_manager.emplaceEntity<
            StdVec<OrientedBox *>>(body_name + "Blockers", StdVec<OrientedBox *>{});

        if (bd.contains("blockers"))
        {
            for (const auto &blocker : bd.at("blockers"))
            {
                blockers.push_back(&config_manager.getEntity<OrientedBox>(blocker.get<std::string>()));
            }
        }

        StdVec<Shape *> &inserts = *config_manager.emplaceEntity<
            StdVec<Shape *>>(body_name + "Inserts", StdVec<Shape *>{});

        if (bd.contains("box_shape_inserts"))
        {
            for (const auto &insert : bd.at("box_shape_inserts"))
            {
                inserts.push_back(&config_manager.getEntity<GeometricShapeBox>(insert.get<std::string>()));
            }
        }

#ifdef SPHINXSYS_3D
        if (bd.contains("cylinder_shape_inserts"))
        {
            for (const auto &insert : bd.at("cylinder_shape_inserts"))
            {
                inserts.push_back(&config_manager.getEntity<GeometricShapeCylinder>(insert.get<std::string>()));
            }
        }
#endif

        real_body.generateParticles<BaseParticles, Lattice>(blockers, inserts);
    }
}
//=================================================================================================//
RelaxationBodyConfig ParticleGeneration::parseRelaxationBodyConfig(std::string body_name, const json &config)
{
    RelaxationBodyConfig relax_body_config;
    relax_body_config.name_ = body_name;
    if (config.contains("level_set"))
    {
        relax_body_config.with_level_set_ = true;
    }

    if (config.contains("dependent_bodies"))
    {
        relax_body_config.dependent_bodies_ = config.at("dependent_bodies").get<std::vector<std::string>>();
    }

    return relax_body_config;
}
//=================================================================================================//
void ParticleGeneration::defineBodyRelations(RelaxationSystem &relaxation_system)
{
    for (const auto &relax_body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(relax_body_config.name_);
        relaxation_system.addInnerRelation(real_body);

        if (!relax_body_config.dependent_bodies_.empty())
        {
            StdVec<RealBody *> dependent_bodies;
            for (const auto &dependent_body_name : relax_body_config.dependent_bodies_)
            {
                RealBody &dependent_body = relaxation_system.getBodyByName<RealBody>(dependent_body_name);
                relaxation_system.addContactRelation(real_body, dependent_body);
            }
        }
    }
}
//=================================================================================================//
ParticleDynamicsGroup &ParticleGeneration::randomizeParticlePositions(
    RelaxationSystem &relaxation_system, HostMethods &host_methods)
{
    auto &randomize_particle_position = host_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        randomize_particle_position.add(
            &host_methods.template addStateDynamics<RandomizeParticlePositionCK>(real_body));
    }
    return randomize_particle_position;
}
//=================================================================================================//
ParticleDynamicsGroup &ParticleGeneration::addDummyBodiesCellLinkedListDynamics(
    RelaxationSystem &relaxation_system, MainMethods &main_methods)
{
    ParticleDynamicsGroup &dummy_cell_linked_list = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.all_bodies_)
    {
        if (!body_config.is_relaxation_body_)
        {
            RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
            dummy_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(real_body));
        }
    }
    return dummy_cell_linked_list;
}
//=================================================================================================//
ParticleDynamicsGroup &ParticleGeneration::addConfigurationDynamics(
    RelaxationSystem &relaxation_system, MainMethods &main_methods)
{
    ParticleDynamicsGroup &configuration_update = main_methods.addParticleDynamicsGroup();

    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        configuration_update.add(&main_methods.addCellLinkedListDynamics(real_body));
    }

    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        auto &inner_relation = relaxation_system.getRelationByName<
            Inner<Relation<RealBody>>>(body_config.name_);

        configuration_update.add(&main_methods.addRelationDynamics(inner_relation));
        for (const auto &db_config : body_config.dependent_bodies_)
        {
            auto &contact_relation = relaxation_system.getRelationByName<
                Contact<Relation<RealBody, RealBody>>>(body_config.name_ + db_config);
            configuration_update.add(&main_methods.addRelationDynamics(contact_relation));
        }
    }
    return configuration_update;
}
//=================================================================================================//
ParticleDynamicsGroup &ParticleGeneration::addRelaxationResidue(
    RelaxationSystem &relaxation_system, EntityManager &config_manager, MainMethods &main_methods)
{
    ParticleDynamicsGroup &relaxation_residue = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        auto &inner_relation = relaxation_system.getRelationByName<
            Inner<Relation<RealBody>>>(body_config.name_);
        auto &residual_dynamics = main_methods.template addInteractionDynamics<
            KernelGradientIntegral, NoKernelCorrectionCK>(inner_relation);

        if (body_config.with_level_set_)
        {
            RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
            LevelSetShape &level_set_shape = config_manager.getEntity<LevelSetShape>(body_config.name_);
            residual_dynamics.template addPostStateDynamics<LevelsetKernelGradientIntegral>(real_body, level_set_shape);
        }

        for (const auto &db_config : body_config.dependent_bodies_)
        {
            auto &contact_relation = relaxation_system.getRelationByName<
                Contact<Relation<RealBody, RealBody>>>(body_config.name_ + db_config);
            residual_dynamics.template addPostContactInteraction<Boundary, NoKernelCorrectionCK>(contact_relation);
        }

        relaxation_residue.add(&residual_dynamics);
    }
    return relaxation_residue;
}
//=================================================================================================//
BaseDynamics<Real> &ParticleGeneration::addRelaxationScaling(
    RelaxationSystem &relaxation_system, EntityManager &config_manager, MainMethods &main_methods)
{

    auto &relaxation_scaling = main_methods.template addReduceDynamicsGroup<ReduceMin<Real>>();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        relaxation_scaling.add(&main_methods.template addReduceDynamics<RelaxationScalingCK>(real_body));
    }
    return relaxation_scaling;
}
//=================================================================================================//
ParticleDynamicsGroup &ParticleGeneration::addRelaxationPositionUpdate(
    RelaxationSystem &relaxation_system, EntityManager &config_manager, MainMethods &main_methods)
{
    ParticleDynamicsGroup &position_update = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        position_update.add(&main_methods.template addStateDynamics<PositionRelaxationCK>(real_body));
        if (body_config.with_level_set_)
        {
            auto *near_body_surface = config_manager.emplaceEntity<NearShapeSurface>(real_body.Name(), real_body);
            position_update.add(&main_methods.template addStateDynamics<LevelsetBounding>(*near_body_surface));
        }
    }
    return position_update;
}
//=================================================================================================//
ParticleDynamicsGroup &ParticleGeneration::addBodyNormalDirection(
    RelaxationSystem &relaxation_system, EntityManager &config_manager, HostMethods &host_methods)
{
    ParticleDynamicsGroup &normal_direction_update = host_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.all_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        if (body_config.is_solid_body_)
        {
            normal_direction_update.add(&host_methods.template addStateDynamics<NormalFromBodyShapeCK>(real_body));
            real_body.getBaseParticles().template addEvolvingVariable<Vecd>("NormalDirection");
            real_body.getBaseParticles().template addEvolvingVariable<Real>("SignedDistance");
        }
    }
    return normal_direction_update;
}
//=================================================================================================//
void ParticleGeneration::addRelaxationConstraintsIfPresent(
    RelaxationSystem &relaxation_system, EntityManager &config_manager,
    MainMethods &main_methods, const json &config)
{
    ParticleDynamicsGroup &relaxation_constraints = main_methods.addParticleDynamicsGroup();

    for (const auto &body_config : bodies_config_.all_bodies_)
    {
        if (!body_config.is_relaxation_body_)
            continue;

        const std::string body_name = body_config.name_;
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_name);

        StdVec<OrientedBox *> &blockers = config_manager.getEntity<StdVec<OrientedBox *>>(body_name + "Blockers");
        for (const auto &blocker : blockers)
        {
            auto &body_part = real_body.addBodyPart<OrientedBoxByParticle>(*blocker);
            relaxation_constraints.add(
                &main_methods.template addStateDynamics<OrientedBoxConstraint, CovariantVectorAxis>(
                    body_part, "KernelGradientIntegral", 0));

            auto &initial_constraint = main_methods.template addStateDynamics<FixConstraintCK>(body_part);
            relaxation_pipeline_.insert_hook(RelaxationHookPoint::Initialization, [&]()
                                             { initial_constraint.exec(); });
        }

        StdVec<Shape *> &inserts = config_manager.getEntity<StdVec<Shape *>>(body_name + "Inserts");
        for (const auto &insert : inserts)
        {
            auto &body_part = real_body.addBodyPart<BodyRegionByParticle>(*insert);
            relaxation_constraints.add(
                &main_methods.template addStateDynamics<FixConstraintCK>(body_part));
        }
    }

    if (config.contains("relaxation_constraints"))
    {

        for (const auto &rc : config.at("relaxation_constraints"))
        {
            const std::string body_name = rc.at("body_name").get<std::string>();
            RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_name);
            OrientedBox &constraint_region = config_manager.getEntity<
                OrientedBox>(rc.at("oriented_box").get<std::string>());
            auto &body_part = real_body.addBodyPart<OrientedBoxByParticle>(constraint_region);
            std::string type = rc.at("type").get<std::string>();
            if (type == "normal")
            {
                relaxation_constraints.add(
                    &main_methods.template addStateDynamics<OrientedBoxConstraint, CovariantVectorAxis>(
                        body_part, "KernelGradientIntegral", 0));

                auto &initial_constraint = main_methods.template addStateDynamics<FixConstraintCK>(body_part);
                relaxation_pipeline_.insert_hook(RelaxationHookPoint::Initialization, [&]()
                                                 { initial_constraint.exec(); });
            }
            else
            {
                relaxation_constraints.add(
                    &main_methods.template addStateDynamics<FixConstraintCK>(body_part));
            }
        }
    }

    relaxation_pipeline_.insert_hook(RelaxationHookPoint::Constraints, [&]()
                                     { relaxation_constraints.exec(); });
}
//=================================================================================================//
} // namespace SPH
