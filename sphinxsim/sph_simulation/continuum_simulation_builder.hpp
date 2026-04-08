#ifndef CONTINUUM_SIMULATION_BUILDER_HPP
#define CONTINUUM_SIMULATION_BUILDER_HPP

#include "continuum_simulation_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType>
ParticleDynamicsGroup &ContinuumSimulationBuilder::addShearForceIntegration(
    EntityManager &entity_manager, MethodContainerType &method_container, InnerRelationType &inner_relation)
{
    auto &continuum_shear_force =
        method_container.addParticleDynamicsGroup()
            .add(&method_container.template addInteractionDynamics<
                  LinearGradient, Vecd>(inner_relation, "Velocity"));

    std::string body_name = inner_relation.getSPHBody().getName();
    if (entity_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        continuum_shear_force.add(
            &method_container.template addInteractionDynamicsOneLevel<
                continuum_dynamics::ShearIntegration, GeneralContinuum>(
                inner_relation, solver_parameters_.hourglass_factor_));
        return continuum_shear_force;
    }

    if (entity_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        continuum_shear_force.add(
            &method_container.template addInteractionDynamicsOneLevel<
                continuum_dynamics::ShearIntegration, J2Plasticity>(
                inner_relation, solver_parameters_.hourglass_factor_));

        return continuum_shear_force;
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addShearForceIntegration: no supported material type found!");
}
//=================================================================================================//
} // namespace SPH
#endif // CONTINUUM_SIMULATION_BUILDER_HPP