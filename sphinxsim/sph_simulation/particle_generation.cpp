#include "particle_generation.hpp"

#include "geometry_builder.h"
#include "io_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
ParticleGeneration::ParticleGeneration() : io_builder_ptr_(std::make_unique<IOBuilder>()) {}
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
    SPHSolver &sph_solver = defineSPHSolver(relaxation_system, config.at("relaxation_parameters"));
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
    auto &randomize_particle_position = randomizeParticlePositions(relaxation_system, host_methods);

    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    auto &body_update_configuration = addConfigurationDynamics(relaxation_system, main_methods);
    auto &relaxation_residual = addRelaxationResidue(relaxation_system, config_manager, main_methods);
    auto &relaxation_scaling = addRelaxationScaling(relaxation_system, config_manager, main_methods);
    auto &update_particle_position = addRelaxationPositionUpdate(relaxation_system, config_manager, main_methods);
    auto &dummy_cell_linked_list = addDummyBodiesCellLinkedListDynamics(relaxation_system, main_methods);
    //----------------------------------------------------------------------
    //	Define simple file input and outputs functions.
    //----------------------------------------------------------------------
    auto &body_state_recorder = io_builder_ptr_->createBodyStatesRecording(
        relaxation_system, config_manager, main_methods, config);
    auto &write_particle_reload_files = main_methods.addIODynamics<ReloadParticleIOCK>(relaxation_system);
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
    if (config.contains("relaxation_constraints"))
    {
        auto &relaxation_constraints = addRelaxationConstraints(
            relaxation_system, config_manager, main_methods, config.at("relaxation_constraints"));
        relaxation_pipeline_.insert_hook(RelaxationHookPoint::Constraints, [&]()
                                         { relaxation_constraints.exec(); });
    }

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
    relaxation_parameters_ = parseRelaxationParameters(config);
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

        CommonBodyConfig common_body_config;
        common_body_config.name_ = body_name;
        Shape &shape = config_manager.getEntity<Shape>(body_name);
        auto &real_body = relaxation_system.addBody<RealBody>(shape, body_name);

        if (bd.contains("relaxation"))
        {
            common_body_config.is_relaxation_body_ = true;
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
            common_body_config.is_solid_body_ = true;
        }

        real_body.generateParticles<BaseParticles, Lattice>();
        bodies_config_.all_bodies_.push_back(common_body_config);
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
std::string ParticleGeneration::getContactRelationName(const RelaxationBodyConfig &body_config)
{
    return body_config.name_ + body_config.dependent_bodies_.front();
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
                dependent_bodies.push_back(&dependent_body);
            }
            relaxation_system.addContactRelation(real_body, dependent_bodies);
        }
    }
}
//=================================================================================================//
} // namespace SPH
