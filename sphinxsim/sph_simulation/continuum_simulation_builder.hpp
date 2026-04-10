#ifndef CONTINUUM_SIMULATION_BUILDER_HPP
#define CONTINUUM_SIMULATION_BUILDER_HPP

#include "continuum_simulation_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType>
BaseDynamics<void> &ContinuumSimulationBuilder::addAcousticStep1stHalf(
    EntityManager &entity_manager, MethodContainerType &method_container, InnerRelationType &inner_relation)
{
    std::string body_name = inner_relation.getSPHBody().getName();
    if (entity_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        using RiemannSolverType = RiemannSolver<GeneralContinuum, GeneralContinuum, NoLimiter>;
        return method_container.template addInteractionDynamics<
            fluid_dynamics::AcousticStep1stHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (entity_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        using RiemannSolverType = RiemannSolver<J2Plasticity, J2Plasticity, NoLimiter>;
        return method_container.template addInteractionDynamics<
            fluid_dynamics::AcousticStep1stHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addAcousticStep1stHalf: no supported material type found!");
}
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType>
BaseDynamics<void> &ContinuumSimulationBuilder::addAcousticStep2ndHalf(
    EntityManager &entity_manager, MethodContainerType &method_container, InnerRelationType &inner_relation)
{
    std::string body_name = inner_relation.getSPHBody().getName();
    if (entity_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        using RiemannSolverType = RiemannSolver<GeneralContinuum, GeneralContinuum, NoLimiter>;
        return method_container.template addInteractionDynamics<
            fluid_dynamics::AcousticStep2ndHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (entity_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        using RiemannSolverType = RiemannSolver<J2Plasticity, J2Plasticity, NoLimiter>;
        return method_container.template addInteractionDynamics<
            fluid_dynamics::AcousticStep2ndHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addAcousticStep1stHalf: no supported material type found!");
}
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
                inner_relation, solver_parameters_.hourglass_factor_,
                solver_parameters_.shear_stress_damping_));
        return continuum_shear_force;
    }

    if (entity_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        continuum_shear_force.add(
            &method_container.template addInteractionDynamicsOneLevel<
                continuum_dynamics::ShearIntegration, J2Plasticity>(
                inner_relation, solver_parameters_.hourglass_factor_,
                solver_parameters_.shear_stress_damping_));

        return continuum_shear_force;
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addShearForceIntegration: no supported material type found!");
}
//=================================================================================================//
template <class MethodContainerType>
void ContinuumSimulationBuilder::addOutputEvolvingVariablesBounds(
    MethodContainerType &method_container, SPHBody &sph_body)
{
    BaseParticles &base_particles = sph_body.getBaseParticles();
    DiscreteVariables &evolving_variables = base_particles.EvolvingVariables();

    // scalar bounds
    constexpr int type_index_Real = DataTypeIndex<Real>::value;
    for (DiscreteVariable<Real> *variable : std::get<type_index_Real>(evolving_variables))
    {
        evolving_variables_names_[0].push_back(variable->Name());
        output_evolving_variables_bounds_[0].push_back(
            &method_container.template addReduceDynamics<MaximumNorm<Real>>(sph_body, variable->Name()));
    }
    // vectors bounds
    constexpr int type_index_Vecd = DataTypeIndex<Vecd>::value;
    for (DiscreteVariable<Vecd> *variable : std::get<type_index_Vecd>(evolving_variables))
    {
        evolving_variables_names_[1].push_back(variable->Name());
        output_evolving_variables_bounds_[1].push_back(
            &method_container.template addReduceDynamics<MaximumNorm<Vecd>>(sph_body, variable->Name()));
    }

    // matrix bounds
    constexpr int type_index_Matd = DataTypeIndex<Matd>::value;
    for (DiscreteVariable<Matd> *variable : std::get<type_index_Matd>(evolving_variables))
    {
        evolving_variables_names_[2].push_back(variable->Name());
        output_evolving_variables_bounds_[2].push_back(
            &method_container.template addReduceDynamics<MaximumNorm<Matd>>(sph_body, variable->Name()));
    }
}
//=================================================================================================//
} // namespace SPH
#endif // CONTINUUM_SIMULATION_BUILDER_HPP