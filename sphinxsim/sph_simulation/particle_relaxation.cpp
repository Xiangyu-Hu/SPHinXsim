#include "particle_relaxation.hpp"

#include "geometry_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
ParticleRelaxation::~ParticleRelaxation() = default;
//=================================================================================================//
void ParticleRelaxation::buildParticleRelaxation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    EntityManager &entity_manager = sim.getEntityManager();
    RelaxationSystem &relaxation_system = defineRelaxationSystem(entity_manager, config);
    //----------------------------------------------------------------------
    addRelaxationBodies(relaxation_system, entity_manager, config.at("relaxation_bodies"));
    defineBodyRelations(relaxation_system);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = defineSPHSolver(relaxation_system, config);
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);

    randomizeParticlePositions(relaxation_system, host_methods);
    auto &body_update_configuration = addConfigurationDynamics(relaxation_system, main_methods);
    auto &relaxation_residual = addRelaxationResidue(relaxation_system, entity_manager, main_methods);
    auto &relaxation_scaling = addRelaxationScaling(relaxation_system, entity_manager, main_methods);
    auto &update_particle_position = addRelaxationPositionUpdate(relaxation_system, entity_manager, main_methods);
    //----------------------------------------------------------------------
    //	Run on CPU after relaxation finished and output results.
    //----------------------------------------------------------------------
    auto &body_normal_direction = addBodyNormalDirection(relaxation_system, entity_manager, main_methods);
    //----------------------------------------------------------------------
    //	Define simple file input and outputs functions.
    //----------------------------------------------------------------------
    auto &body_state_recorder = main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(relaxation_system);
    auto &write_particle_reload_files = main_methods.addIODynamics<ReloadParticleIOCK>(relaxation_system);
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
            relaxation_pipeline_.run_hooks(RelaxationHookPoint::Initialization);

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
            std::cout << "The physics relaxation process finish !" << std::endl;
            body_normal_direction.exec();
            write_particle_reload_files.writeToFile();
        });

    //----------------------------------------------------------------------
    // Define optional methods using hooking point in stage pipelines.
    //----------------------------------------------------------------------
    if (config.at("relaxation_constraints"))
    {
        auto &relaxation_constraints = addRelaxationConstraints(
            relaxation_system, entity_manager, main_methods, config.at("relaxation_constraints"));
        relaxation_pipeline_.insert_hook(RelaxationHookPoint::Constraints, [&]()
                                         { relaxation_constraints.exec(); });
    }
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
        system_config.system_bounds_, system_config.particle_spacing_);
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
    for (const auto &rb : config)
    {
        RelaxationBodyConfig body_config;
        body_config.name_ = rb.at("name").get<std::string>();
        Shape &shape = entity_manager.getEntityByName<Shape>(body_config.name_);
        auto &real_body = relaxation_system.addBody<RealBody>(shape, body_config.name_);

        if (rb.contains("with_level_set"))
        {
            body_config.with_level_set_ = true;
            LevelSetShape &level_set_shape = real_body.defineBodyLevelSetShape(par_ck, 2.0).writeLevelSet();
            entity_manager.addEntity(body_config.name_, &level_set_shape);
            entity_manager.emplaceEntity<NearShapeSurface>(real_body.getName(), real_body);
        }

        if (rb.contains("solid_body"))
        {
            body_config.is_solid_body_ = true;
        }

        if (rb.contains("contact_bodies"))
        {
            for (const auto &contact_body : rb.at("contact_bodies"))
            {
                body_config.contact_bodies_.push_back(contact_body.get<std::string>());
            }
        }
        real_body.generateParticles<BaseParticles, Lattice>();
        bodies_config_.relaxation_bodies_.push_back(body_config);
    }
}
//=================================================================================================//
void ParticleRelaxation::defineBodyRelations(RelaxationSystem &relaxation_system)
{
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        relaxation_system.addInnerRelation(real_body);

        if (!body_config.contact_bodies_.empty())
        {
            StdVec<RealBody *> contact_bodies;
            for (const auto &contact_body_name : body_config.contact_bodies_)
            {
                RealBody &contact_body = relaxation_system.getBodyByName<RealBody>(contact_body_name);
                contact_bodies.push_back(&contact_body);
            }
            relaxation_system.addContactRelation(real_body, contact_bodies);
        }
    }
}
//=================================================================================================//
} // namespace SPH
