#include "fluid_dynamics_builder.h"
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

    for (const auto &cb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(cb->name_);
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

    for (const auto &cb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(cb->name_);
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

    for (const auto &cb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(cb->name_);
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
    auto &fluid_solver_parameters = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    for (const auto &cb : fluid_bodies_config)
    {
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(cb->name_);
        acoustic_time_step.add(
            &main_methods.addReduceDynamics<
                AcousticTimeStepCK<WeaklyCompressibleFluid>>(
                fluid_body, fluid_solver_parameters.acoustic_cfl_));
    }
    return acoustic_time_step;
} //=================================================================================================//
} // namespace SPH
