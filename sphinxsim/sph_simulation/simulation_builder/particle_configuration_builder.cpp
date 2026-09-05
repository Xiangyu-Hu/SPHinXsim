#include "base_simulation_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void SimulationBuilder::buildUpdateConfiguration(SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    buildCellLinkedListDynamics(sim, main_methods, config);
    buildFluidRelationDynamics(sim, main_methods, config);
    buildContinuumRelationDynamics(sim, main_methods, config);
    buildSolidRelationDynamics(sim, main_methods, config);
}
//=================================================================================================//
void SimulationBuilder::buildCellLinkedListDynamics(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &update_cell_linked_list = main_methods.addParticleDynamicsGroup();

    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        update_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(fluid_body));
    }

    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>("ContinuumBodiesConfig");
    for (const auto &cb : continuum_bodies_config)
    {
        auto &continuum_body = sph_system.getBodyByName<RealBody>(cb->name_);
        update_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(continuum_body));
    }

    auto &static_cell_linked_list = main_methods.addParticleDynamicsGroup();
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    for (const auto &sb : solid_bodies_config)
    {

        auto &solid_body = sph_system.getBodyByName<SolidBody>(sb->name_);
        sb->is_moving_
            ? update_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(solid_body))
            : static_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(solid_body));
    }

    auto &initialization_pipeline = sim.getInitializationPipeline();
    if (static_cell_linked_list.hasDynamics())
    {
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialUpdateConfiguration, [&]()
            { static_cell_linked_list.exec(); });
    }

    addUpdateConfigurationDynamicsToPipeline(sim, config_manager, update_cell_linked_list);
}
//=================================================================================================//
void SimulationBuilder::buildFluidRelationDynamics(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    if (fluid_bodies_config.empty())
        return;

    auto &sph_system = sim.getSPHSystem();
    auto &update_all_fluid_relations = main_methods.addParticleDynamicsGroup();

    for (const auto &fb_src : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb_src->name_);
        auto &fluid_inner = sph_system.addInnerRelation(fluid_body);
        auto &update_fluid_relation = main_methods.addParticleDynamicsGroup();
        update_fluid_relation.add(&main_methods.addRelationDynamics(fluid_inner));

        for (const auto &fb_tgt : fluid_bodies_config)
        {
            if (fb_src->name_ != fb_tgt->name_)
            {
                auto &fluid_body_target = sph_system.getBodyByName<FluidBody>(fb_tgt->name_);
                auto &fluid_contact = sph_system.addContactRelation(fluid_body, fluid_body_target);
                update_fluid_relation.add(&main_methods.addRelationDynamics(fluid_contact));
            }
        }

        auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>("ContinuumBodiesConfig");
        for (const auto &cb_tgt : continuum_bodies_config)
        {
            auto &continuum_body_target = sph_system.getBodyByName<RealBody>(cb_tgt->name_);
            auto &fluid_contact = sph_system.addContactRelation(fluid_body, continuum_body_target);
            update_fluid_relation.add(&main_methods.addRelationDynamics(fluid_contact));
        }

        auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
        for (const auto &sb_tgt : solid_bodies_config)
        {
            auto &solid_body_target = sph_system.getBodyByName<SolidBody>(sb_tgt->name_);
            auto &fluid_contact = sph_system.addContactRelation(fluid_body, solid_body_target);
            update_fluid_relation.add(&main_methods.addRelationDynamics(fluid_contact));
        }

        update_all_fluid_relations.add(&update_fluid_relation);
    }

    addUpdateConfigurationDynamicsToPipeline(sim, config_manager, update_all_fluid_relations);
}
//=================================================================================================//
void SimulationBuilder::buildContinuumRelationDynamics(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>("ContinuumBodiesConfig");
    if (continuum_bodies_config.empty())
        return;

    auto &sph_system = sim.getSPHSystem();
    auto &update_all_continuum_relations = main_methods.addParticleDynamicsGroup();

    for (const auto &cb_src : continuum_bodies_config)
    {
        auto &continuum_body = sph_system.getBodyByName<RealBody>(cb_src->name_);
        auto &continuum_inner = sph_system.addInnerRelation(continuum_body);
        auto &update_continuum_relation = main_methods.addParticleDynamicsGroup();
        update_continuum_relation.add(&main_methods.addRelationDynamics(continuum_inner));

        for (const auto &cb_tgt : continuum_bodies_config)
        {
            if (cb_src->name_ != cb_tgt->name_)
            {
                auto &continuum_body_target = sph_system.getBodyByName<RealBody>(cb_tgt->name_);
                auto &continuum_contact = sph_system.addContactRelation(continuum_body, continuum_body_target);
                update_continuum_relation.add(&main_methods.addRelationDynamics(continuum_contact));
            }
        }

        auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
        for (const auto &fb_tgt : fluid_bodies_config)
        {
            auto &fluid_body_target = sph_system.getBodyByName<FluidBody>(fb_tgt->name_);
            auto &continuum_contact = sph_system.addContactRelation(continuum_body, fluid_body_target);
            update_continuum_relation.add(&main_methods.addRelationDynamics(continuum_contact));
        }

        auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
        for (const auto &sb_tgt : solid_bodies_config)
        {
            auto &solid_body_target = sph_system.getBodyByName<SolidBody>(sb_tgt->name_);
            auto &continuum_contact = sph_system.addContactRelation(continuum_body, solid_body_target);
            update_continuum_relation.add(&main_methods.addRelationDynamics(continuum_contact));
        }

        update_all_continuum_relations.add(&update_continuum_relation);
    }

    addUpdateConfigurationDynamicsToPipeline(sim, config_manager, update_all_continuum_relations);
}
//=================================================================================================//
void SimulationBuilder::buildSolidRelationDynamics(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &config_manager = sim.getConfigManager();
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    if (solid_bodies_config.empty())
        return;

    auto &sph_system = sim.getSPHSystem();
    auto &update_all_contact_relations = main_methods.addParticleDynamicsGroup();
    auto &total_lagrangian_relations = main_methods.addParticleDynamicsGroup();

    for (const auto &sb_src : solid_bodies_config)
    {
        auto &solid_body = sph_system.getBodyByName<SolidBody>(sb_src->name_);
        if (sb_src->has_dynamics_)
        {
            auto &solid_inner = sph_system.addInnerRelation(solid_body, ConfigType::Lagrangian);
            total_lagrangian_relations.add(&main_methods.addRelationDynamics(solid_inner));
        }

        if (sb_src->is_interactive_)
        {
            auto &update_contact_relation = main_methods.addParticleDynamicsGroup();
            for (const auto &sb_tgt : solid_bodies_config)
            {
                if (sb_src->name_ != sb_tgt->name_)
                {
                    auto &solid_body_target = sph_system.getBodyByName<SolidBody>(sb_tgt->name_);
                    if (!sb_src->is_moving_ && !sb_tgt->is_moving_)
                    {
                        auto &solid_contact = sph_system.addContactRelation(
                            solid_body, solid_body_target, ConfigType::Lagrangian);
                        total_lagrangian_relations.add(&main_methods.addRelationDynamics(solid_contact));
                    }
                    else
                    {
                        auto &solid_contact = sph_system.addContactRelation(solid_body, solid_body_target);
                        update_contact_relation.add(&main_methods.addRelationDynamics(solid_contact));
                    }
                }
            }

            auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
            for (const auto &fb_tgt : fluid_bodies_config)
            {
                auto &fluid_body_target = sph_system.getBodyByName<FluidBody>(fb_tgt->name_);
                auto &solid_contact = sph_system.addContactRelation(solid_body, fluid_body_target);
                update_contact_relation.add(&main_methods.addRelationDynamics(solid_contact));
            }

            auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>("ContinuumBodiesConfig");
            for (const auto &cb_tgt : continuum_bodies_config)
            {
                auto &continuum_body_target = sph_system.getBodyByName<RealBody>(cb_tgt->name_);
                auto &solid_contact = sph_system.addContactRelation(solid_body, continuum_body_target);
                update_contact_relation.add(&main_methods.addRelationDynamics(solid_contact));
            }

            update_all_contact_relations.add(&update_contact_relation);
        }
    }

    auto &initialization_pipeline = sim.getInitializationPipeline();
    if (total_lagrangian_relations.hasDynamics())
    {
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialUpdateConfiguration, [&]()
            { total_lagrangian_relations.exec(); });
    }

    addUpdateConfigurationDynamicsToPipeline(sim, config_manager, update_all_contact_relations);
}
//=================================================================================================//
void SimulationBuilder::addUpdateConfigurationDynamicsToPipeline(
    SPHSimulation &sim, EntityManager &config_manager, ParticleDynamicsGroup &configuration_dynamics)
{
    if (!configuration_dynamics.hasDynamics())
        return;

    auto &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialUpdateConfiguration, [&]()
        { configuration_dynamics.exec(); });

    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::UpdateConfiguration, [&]()
        { configuration_dynamics.exec(); });

    if (!config_manager.hasEntity<RestartConfig>("RestartConfig"))
        return;

    RestartConfig &restart_config = config_manager.getEntity<RestartConfig>("RestartConfig");
    if (restart_config.restore_step_ != 0)
    {
        initialization_pipeline.insert_hook(
            InitializationHookPoint::UpdateConfigurationAfterRestart, [&]()
            { configuration_dynamics.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH
