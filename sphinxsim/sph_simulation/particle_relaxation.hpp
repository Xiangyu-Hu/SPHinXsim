#ifndef PARTICLE_RELAXATION_BUILDER_HPP
#define PARTICLE_RELAXATION_BUILDER_HPP

#include "particle_relaxation.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
void ParticleRelaxation::randomizeParticlePositions(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
{
    for (const auto &body_name : bodies_config_.main_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_name);
        host_methods.addStateDynamics<RandomizeParticlePositionCK>(real_body).exec()
    }
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addDummyBodiesCellLinkedListDynamics(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &dummy_cell_linked_list = main_methods.addParticleDynamicsGroup();
    for (const auto &name : bodies_config_.dummy_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(name);
        dummy_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(real_body));
    }
    return dummy_cell_linked_list;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addConfigurationDynamics(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
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

        if (body_config.contact_bodies_.empty())
        {
            configuration_update.add(&main_methods.addRelationDynamics(inner_relation));
        }
        else
        {
            auto &contact_relation = relaxation_system.getRelationByName<
                Contact<Relation<RealBody, RealBody>>>(body_config.name_);
            configuration_update.add(&main_methods.addRelationDynamics(inner_relation, contact_relation));
        }
    }
    return configuration_update;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addRelaxationResidue(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &relaxation_residue = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        auto &inner_relation = relaxation_system.getRelationByName<
            Inner<Relation<RealBody>>>(body_config.name_);
        auto &residual_dynamics = main_methods.addInteractionDynamics<
            KernelGradientIntegral, NoKernelCorrectionCK>(input_body_inner);

        if (body_config.with_level_set_)
        {
            RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
            LevelSetShape &level_set_shape = entity_manager.getEntityByName<LevelSetShape>(body_config.name_);
            residual_dynamics.addPostStateDynamics<LevelsetKernelGradientIntegral>(real_body, level_set_shape);
        }

        if (!body_config.contact_bodies_.empty())
        {
            auto &contact_relation = relaxation_system.getRelationByName<
                Contact<Relation<RealBody, RealBody>>>(body_config.name_);
            residual_dynamics.addPostContactInteraction<Boundary, NoKernelCorrectionCK>(contact_relation));
        }
        relaxation_residue.add(&residual_dynamics);
    }
    return relaxation_residue;
}
//=================================================================================================//
template <class MethodContainerType>
ReduceDynamicsGroup<ReduceMin> ParticleRelaxation::addRelaxationScaling(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{

    ReduceDynamicsGroup<ReduceMin> relaxation_scaling;
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        relaxation_scaling.add(&main_methods.addReduceDynamics<RelaxationScalingCK>(real_body));
    }
    return relaxation_scaling;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addRelaxationPositionUpdate(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &position_update = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        position_update.add(&main_methods.addStateDynamics<PositionRelaxationCK>(real_body));
        if (body_config.with_level_set_)
        {
            auto &near_body_surface = entity_manager.getEntityByName<NearShapeSurface>(body_config.name_);
            position_update.add(&main_methods.addStateDynamics<LevelsetBounding>(near_body_surface));
        }
    }
    return position_update;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addBodyNormalDirection(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &normal_direction_update = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        normal_direction_update.add(&main_methods.addStateDynamics<BodyNormalDirectionCK>(real_body));
    }
    return normal_direction_update;
}
//=================================================================================================//
} // namespace SPH
#endif // PARTICLE_RELAXATION_BUILDER_HPP
