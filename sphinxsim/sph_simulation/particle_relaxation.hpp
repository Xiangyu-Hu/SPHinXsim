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
        auto &body_inner = *relaxation_system.getRelationByName<
            InnerRelation<RealBody>>(relax_body->getName());
        auto &body_contact = *relaxation_system.getRelationByName<
            Contact<Relation<RealBody, RealBody>>>(relax_body->getName());
        configuration_update.add(&main_methods.addRelationDynamics(body_inner, body_contact));
    }

    return configuration_update;
}
//=================================================================================================//
} // namespace SPH
#endif // PARTICLE_RELAXATION_BUILDER_HPP
