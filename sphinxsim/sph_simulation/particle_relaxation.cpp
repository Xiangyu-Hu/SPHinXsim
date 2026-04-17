#include "particle_relaxation.h"

#include "geometry_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void ParticleRelaxation::buildParticleRelaxation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    EntityManager &entity_manager = sim.getEntityManager();
    RelaxationSystem &relaxation_system = defineRelaxationSystem(entity_manager, config);
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape and particles.
    //----------------------------------------------------------------------
    addRelaxationBodies(relaxation_system, entity_manager, config);
    //----------------------------------------------------------------------
    //	Define body relation map.
    //	The relations give the topological connections within a body
    //  or with other bodies within interaction range.
    //  Generally, we first define all the inner relations, then the contact relations.
    //----------------------------------------------------------------------
    defineBodyRelations(relaxation_system, entity_manager, config);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = defineSPHSolver(relaxation_system, config);
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
    StdVec<RealBody *> relaxation_bodies = relaxation_system.collectBodies<RealBody>();
    host_methods.addStateDynamics<RandomizeParticlePositionCK>(relaxation_bodies).exec();

    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    auto &body_update_configuration = addConfigurationDynamics(relaxation_system, main_methods);

    LevelSetShape &level_set_shape = entity_manager.getEntityByName<LevelSetShape>(relax_body.getName());
    auto &relaxation_residual =
        main_methods.addInteractionDynamics<KernelGradientIntegral, NoKernelCorrectionCK>(body_inner)
            .addPostStateDynamics<LevelsetKernelGradientIntegral>(relax_body, level_set_shape);
    auto &relaxation_scaling = main_methods.addReduceDynamics<RelaxationScalingCK>(relax_body);
    auto &update_particle_position = main_methods.addStateDynamics<PositionRelaxationCK>(relax_body);

    NearShapeSurface *near_body_surface = entity_manager.emplaceEntity<NearShapeSurface>(relax_body.getName(), relax_body);
    auto &level_set_bounding = main_methods.addStateDynamics<LevelsetBounding>(*near_body_surface);
    //----------------------------------------------------------------------
    //	Run on CPU after relaxation finished and output results.
    //----------------------------------------------------------------------
    auto &body_normal_direction = host_methods.addStateDynamics<NormalFromBodyShapeCK>(relax_body);
    //----------------------------------------------------------------------
    //	Define simple file input and outputs functions.
    //----------------------------------------------------------------------
    auto &body_state_recorder = main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(relaxation_system);
    auto &write_particle_reload_files = main_methods.addIODynamics<ReloadParticleIOCK>(StdVec<SPHBody *>{&relax_body});
    write_particle_reload_files.addToReload<Vecd>(relax_body, "NormalDirection");
    //----------------------------------------------------------------------
    //	First output before the particle relaxation.
    //----------------------------------------------------------------------
    body_state_recorder.writeToFile(0);
    //----------------------------------------------------------------------
    //	Define particle relaxation simulation.
    //----------------------------------------------------------------------
    ParticleRelaxation::updateRelaxationParameters(sim, config);
    relaxation_pipeline_.main_steps.push_back(
        [&]()
        {
            UnsignedInt ite_p = 0;
            while (ite_p < relaxation_parameters_.total_iterations)
            {
                body_update_configuration.exec();

                relaxation_residual.exec();
                Real relaxation_step = relaxation_scaling.exec();
                update_particle_position.exec(relaxation_step);
                level_set_bounding.exec();

                ite_p += 1;
                if (ite_p % 100 == 0)
                {
                    std::cout << std::fixed << std::setprecision(9) << "Relaxation steps N = " << ite_p << "\n";
                    body_state_recorder.writeToFile(ite_p);
                }
            }
            std::cout << "The physics relaxation process finish !" << std::endl;
            body_normal_direction.exec();
            write_particle_reload_files.writeToFile();
        });
}
//=================================================================================================//
void ParticleRelaxation::updateRelaxationParameters(SPHSimulation &sim, const json &config)
{
    if (config.contains("relaxation_parameters"))
    {
        const json &relaxation_parameters_json = config.at("relaxation_parameters");
        if (relaxation_parameters_json.contains("total_iterations"))
            relaxation_parameters_.total_iterations =
                relaxation_parameters_json.at("total_iterations").get<UnsignedInt>();
    }
}
//=================================================================================================//
void ParticleRelaxation::runRelaxation()
{
    for (auto &step : relaxation_pipeline_.main_steps)
    {
        step(); // each step touches all cells internally
    }
}
//=================================================================================================//
RelaxationSystem &ParticleRelaxation::defineRelaxationSystem(
    EntityManager &entity_manager, const json &config)
{
    auto &system_config = entity_manager.getEntityByName<SystemDomainConfig>("SystemDomainConfig");
    relaxation_system_ptr_ = std::make_unique<RelaxationSystem>(
        system_config.system_domain_bounds_, system_config.particle_spacing_);
    return *relaxation_system_ptr_.get();
}
//=================================================================================================//
SPHSolver &ParticleRelaxation::defineSPHSolver(RelaxationSystem &relaxation_system, const json &config)
{
    sph_solver_ptr_ = std::make_unique<SPHSolver>(relaxation_system);
    return *sph_solver_ptr_.get();
}
//=================================================================================================//
void ParticleRelaxation::addRelaxationBodies(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, const json &config)
{
    for (const auto &rb : config.at("relaxation_bodies"))
    {
        const std::string name = rb.at("name").get<std::string>();
        Shape &shape = entity_manager.getEntityByName<Shape>(name);
        auto &relaxation_body = relaxation_system.addBody<RealBody>(shape, name);
        relaxation_body.generateParticles<BaseParticles, Lattice>();
        LevelSetShape &level_set_shape = relaxation_body.defineBodyLevelSetShape(par_ck, 2.0).writeLevelSet();
        entity_manager.addEntity(name, &level_set_shape);
    }
}
//=================================================================================================//
void ParticleRelaxation::defineBodyRelations(RelaxationSystem &relaxation_system, const json &config)
{
    StdVec<RealBody *> relaxation_bodies = relaxation_system.collectBodies<RealBody>();
    for (const auto &relax_body : relaxation_bodies)
    {
        relaxation_system.addInnerRelation(*relax_body);
    }

    if (config.contains("contact_relations"))
    {
        for (const auto &cr : config.at("contact_relations"))
        {
            if (cr.size() < 2)
            {
                throw std::runtime_error("Contact relation should have at least two bodies.");
            }

            const std::string source_body_name = cr.at(0).get<std::string>();
            RealBody &source_body = relaxation_system.getBodyByName<RealBody>(source_body_name);
            StdVec<RealBody *> target_bodies;
            for (size_t i = 1; i < cr.size(); ++i)
            {
                const std::string target_body_name = cr.at(i).get<std::string>();
                target_bodies.push_back(&relaxation_system.getBodyByName<RealBody>(target_body_name));
            }
            relaxation_system.addContactRelation(source_body, target_bodies);
        }
    }
}
//=================================================================================================//
} // namespace SPH
