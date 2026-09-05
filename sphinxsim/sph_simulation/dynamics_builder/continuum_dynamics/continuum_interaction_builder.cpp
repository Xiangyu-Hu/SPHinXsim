#include "continuum_interaction_builder.hpp"

namespace SPH
{
//=================================================================================================//
BaseDynamics<void> &ContinuumDynamicsBuilder::addAcousticStep1stHalf(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &acoustic_step_1st_half = main_methods.addParticleDynamicsGroup();

    for (const auto &cb : continuum_bodies_config)
    {
        std::string body_name = cb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<RealBody>>>(body_name);
        acoustic_step_1st_half.add(&addAcousticStep1stHalfForOneBody(sim, inner_relation, main_methods));
    }
    return acoustic_step_1st_half;
}
//=================================================================================================//
BaseDynamics<void> &ContinuumDynamicsBuilder::addAcousticStep2ndHalf(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &acoustic_step_2nd_half = main_methods.addParticleDynamicsGroup();

    for (const auto &cb : continuum_bodies_config)
    {
        std::string body_name = cb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<RealBody>>>(body_name);
        acoustic_step_2nd_half.add(&addAcousticStep2ndHalfForOneBody(sim, inner_relation, main_methods));
    }
    return acoustic_step_2nd_half;
}
//=================================================================================================//
} // namespace SPH
