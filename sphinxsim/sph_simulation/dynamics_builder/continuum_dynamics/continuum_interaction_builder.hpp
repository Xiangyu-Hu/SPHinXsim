#ifndef CONTINUUM_DYNAMICS_BUILDER_HPP
#define CONTINUUM_DYNAMICS_BUILDER_HPP

#include "continuum_dynamics_builder.h"

#include "all_continuum_dynamics_ck.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class InnerRelationType>
BaseDynamics<void> &ContinuumDynamicsBuilder::addAcousticStep1stHalfForOneBody(
    SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    std::string body_name = inner_relation.getSPHBody().Name();

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        using RiemannSolverType = RiemannSolver<J2Plasticity, J2Plasticity, NoLimiter>;
        return main_methods.template addInteractionDynamics<
            fluid_dynamics::AcousticStep1stHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        using RiemannSolverType = RiemannSolver<PlasticContinuum, PlasticContinuum, TruncatedLinear>;
        auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
            continuum_dynamics::PlasticAcousticStep1stHalf,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);

        auto &sph_system = sim.getSPHSystem();
        auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
        for (const auto &sb_tgt : solid_bodies_config)
        {
            std::string relation_name = body_name + sb_tgt->name_;
            auto &contact_relation = sph_system.getRelationByName<
                Contact<Relation<RealBody, SolidBody>>>(relation_name);
            complex_dynamics.template addPostContactInteraction<
                Wall, RiemannSolverType, NoKernelCorrectionCK>(contact_relation);
        }
        return complex_dynamics;
    }

    throw std::runtime_error(
        "ContinuumDynamicsBuilder::addAcousticStep1stHalfForOneBody: no supported material type found!");
}
//=================================================================================================//
template <class InnerRelationType>
BaseDynamics<void> &ContinuumDynamicsBuilder::addAcousticStep2ndHalfForOneBody(
    SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    std::string body_name = inner_relation.getSPHBody().Name();

    if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
    {
        using RiemannSolverType = RiemannSolver<J2Plasticity, J2Plasticity, NoLimiter>;
        return main_methods.template addInteractionDynamics<
            fluid_dynamics::AcousticStep2ndHalf, OneLevel,
            RiemannSolverType, NoKernelCorrectionCK>(inner_relation);
    }

    if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
    {
        using RiemannSolverType = RiemannSolver<PlasticContinuum, PlasticContinuum, TruncatedLinear>;
        auto &continuum_solver_parameters = config_manager.getEntity<
            ContinuumSolverParameters>("ContinuumSolverParameters");
        auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
            continuum_dynamics::PlasticAcousticStep2ndHalf,
            RiemannSolverType, NoKernelCorrectionCK>(
            inner_relation, continuum_solver_parameters.plastic_riemann_dissipation_factor_);

        auto &sph_system = sim.getSPHSystem();
        auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
        for (const auto &sb_tgt : solid_bodies_config)
        {
            std::string relation_name = body_name + sb_tgt->name_;
            auto &contact_relation = sph_system.getRelationByName<
                Contact<Relation<RealBody, SolidBody>>>(relation_name);
            complex_dynamics.template addPostContactInteraction<
                Wall, RiemannSolverType, NoKernelCorrectionCK>(contact_relation);
        }
        return complex_dynamics;
    }

    throw std::runtime_error(
        "ContinuumDynamicsBuilder::addAcousticStep2ndHalfForOneBody: no supported material type found!");
}
//=================================================================================================//
} // namespace SPH
#endif // CONTINUUM_DYNAMICS_BUILDER_HPP
