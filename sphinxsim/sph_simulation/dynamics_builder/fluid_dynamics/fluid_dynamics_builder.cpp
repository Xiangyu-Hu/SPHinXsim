#include "fluid_dynamics_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addAdvectionStepSetup(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &advection_step_setup = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        advection_step_setup.add(&main_methods.addStateDynamics<AdvectionStepSetup>(
            fluid_body));
    }
    return advection_step_setup;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addUpdateParticlePosition(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &update_particle_position = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        update_particle_position.add(
            &main_methods.addStateDynamics<UpdateParticlePosition>(fluid_body));
    }
    return update_particle_position;
}
//=================================================================================================//
BaseDynamics<Real> &FluidDynamicsBuilder::addAdvectionTimeStep(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &advection_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &fluid_solver_parameters = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        advection_time_step.add(&main_methods.addReduceDynamics<AdvectionTimeStepCK>(
            fluid_body, Real(1), fluid_solver_parameters.advection_cfl_));
    }
    return advection_time_step;
}
//=================================================================================================//
BaseDynamics<Real> &FluidDynamicsBuilder::addAcousticTimeStep(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();

    for (const auto &fb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(fb->name_);
        acoustic_time_step.add(&addAcousticTimeStepForOneBody(sim, fluid_body, main_methods));
    }
    return acoustic_time_step;
}
//=================================================================================================//
BaseDynamics<Real> &FluidDynamicsBuilder::addAcousticTimeStepForOneBody(
    SPHSimulation &sim, FluidBody &fluid_body, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    if (fluid_body.isMatterMaterial<WeaklyCompressibleFluid>())
    {
        return main_methods.addReduceDynamics<
            AcousticTimeStepCK<WeaklyCompressibleFluid>>(
            fluid_body, fluid_solver_config.acoustic_cfl_);
    }

    if (fluid_body.isMatterMaterial<WeaklyCompressibleMixture>())
    {
        return main_methods.addReduceDynamics<
            AcousticTimeStepCK<WeaklyCompressibleMixture>>(
            fluid_body, fluid_solver_config.acoustic_cfl_);
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::addAcousticTimeStepForOneBody: no supported material type found!");
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticStep1stHalf(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_step_1st_half = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        acoustic_step_1st_half.add(&addAcousticHalfStepForOneBody<AcousticStep1stHalf>(
            sim, inner_relation, main_methods));
    }
    return acoustic_step_1st_half;
}
//=================================================================================================//
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticStep2ndHalf(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    auto &acoustic_step_2nd_half = main_methods.addParticleDynamicsGroup();

    for (const auto &fb : fluid_bodies_config)
    {
        std::string body_name = fb->name_;
        auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);
        acoustic_step_2nd_half.add(&addAcousticHalfStepForOneBody<AcousticStep2ndHalf>(
            sim, inner_relation, main_methods));
    }
    return acoustic_step_2nd_half;
}
//=================================================================================================//
} // namespace SPH
