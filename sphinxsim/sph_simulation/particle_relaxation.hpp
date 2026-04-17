#ifndef PARTICLE_RELAXATION_BUILDER_HPP
#define PARTICLE_RELAXATION_BUILDER_HPP

#include "particle_relaxation.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addConfigurationDynamics(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &configuration_update = main_methods.addParticleDynamicsGroup();

    StdVec<RealBody *> relaxation_bodies = relaxation_system.collectBodies<RealBody>();
    configuration_update.add(&main_methods.addCellLinkedListDynamics(relaxation_bodies));
    for (auto *relax_body : relaxation_bodies)
    {
        auto &body_inner = relaxation_system.getRelationByName<
            InnerRelation<RealBody>>(relax_body->getName());
        if (body_inner != nullptr)
        {
            configuration_update.add(&main_methods.addRelationDynamics(body_inner));
        }

        auto &body_contact = relaxation_system.getRelationByName<
            Contact<Relation<RealBody, RealBody>>>(relax_body->getName());
        if (body_contact != nullptr)
        {
            configuration_update.add(&main_methods.addRelationDynamics(body_contact));
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
    StdVec<RealBody *> relaxation_bodies = relaxation_system.collectBodies<RealBody>();
    for (auto *relax_body : relaxation_bodies)
    {
        LevelSetShape &level_set_shape = entity_manager.getEntityByName<LevelSetShape>(relax_body.getName());
        main_methods.addInteractionDynamics<KernelGradientIntegral, NoKernelCorrectionCK>(body_inner)
            .addPostStateDynamics<LevelsetKernelGradientIntegral>(relax_body, level_set_shape);
    }
}
//=================================================================================================//
} // namespace SPH
#endif // PARTICLE_RELAXATION_BUILDER_HPP
