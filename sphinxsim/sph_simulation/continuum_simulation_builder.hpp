#ifndef CONTINUUM_SIMULATION_BUILDER_HPP
#define CONTINUUM_SIMULATION_BUILDER_HPP

#include "continuum_simulation_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType, class InnerRelationType>
BaseDynamics<void> &ContinuumSimulationBuilder::addAcousticStep1stHalf(
    EntityManager &config_manager, MethodContainerType &method_container, InnerRelationType &inner_relation)
{
    std::string body_name = inner_relation.getSPHBody().getName();
    if (config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        using RiemannSolverType = RiemannSolver<GeneralContinuum, GeneralContinuum, NoLimiter>;
        return method_container.template addInteractionDynamics<
            fluid_dynamics::AcousticStep1stHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
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
    EntityManager &config_manager, MethodContainerType &method_container, InnerRelationType &inner_relation)
{
    std::string body_name = inner_relation.getSPHBody().getName();
    if (config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        using RiemannSolverType = RiemannSolver<GeneralContinuum, GeneralContinuum, NoLimiter>;
        return method_container.template addInteractionDynamics<
            fluid_dynamics::AcousticStep2ndHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
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
    EntityManager &config_manager, MethodContainerType &method_container, InnerRelationType &inner_relation)
{
    auto &continuum_solver_parameters = config_manager.getEntity<
        ContinuumSolverParameters>("ContinuumSolverParameters");
    auto &continuum_shear_force =
        method_container.addParticleDynamicsGroup()
            .add(&method_container.template addInteractionDynamics<
                  LinearGradient, Vecd>(inner_relation, "Velocity"));

    std::string body_name = inner_relation.getSPHBody().getName();
    if (config_manager.hasEntity<GeneralContinuum>(body_name + "GeneralContinuum"))
    {
        continuum_shear_force.add(
            &method_container.template addInteractionDynamicsOneLevel<
                continuum_dynamics::ShearIntegration, GeneralContinuum>(
                inner_relation, continuum_solver_parameters.hourglass_factor_,
                continuum_solver_parameters.shear_stress_damping_));
        return continuum_shear_force;
    }

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        continuum_shear_force.add(
            &method_container.template addInteractionDynamicsOneLevel<
                continuum_dynamics::ShearIntegration, J2Plasticity>(
                inner_relation, continuum_solver_parameters.hourglass_factor_,
                continuum_solver_parameters.shear_stress_damping_));

        return continuum_shear_force;
    }

    throw std::runtime_error(
        "ContinuumSimulationBuilder::addShearForceIntegration: no supported material type found!");
}
//=================================================================================================//
} // namespace SPH
#endif // CONTINUUM_SIMULATION_BUILDER_HPP