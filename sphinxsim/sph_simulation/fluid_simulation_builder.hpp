#ifndef FLUID_SIMULATION_BUILDER_HPP
#define FLUID_SIMULATION_BUILDER_HPP

#include "fluid_simulation_builder.h"

#include "geometry_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
void FluidSimulationBuilder::addBoundaryConditions(
    SPHSimulation &sim, MethodContainerType &method_container, const json &config)
{
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();
    EntityManager &entity_manager = sim.getEntityManager();

    const std::string body_name = config.at("body_name").get<std::string>();
    FluidBody &fluid_body = sim.getSPHSystem().getBodyByName<FluidBody>(body_name);
    const std::string type = config.at("type").get<std::string>();

    if (type == "emitter")
    { // must be aligned box for emitter
        AlignedBox &aligned_box = entity_manager.getEntityByName<AlignedBox>(
            config.at("aligned_box").get<std::string>());
        auto &emitter = fluid_body.addBodyPart<AlignedBoxByParticle>(aligned_box);
        auto &inflow_condition = method_container.template addStateDynamics<
            fluid_dynamics::EmitterInflowConditionCK, ConstantInflowSpeed>(
            emitter, config.at("inflow_speed").get<Real>());
        auto &injection = method_container.template addStateDynamics<
            fluid_dynamics::EmitterInflowInjectionCK>(emitter);

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryConditions, [&]()
            { inflow_condition.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { injection.exec(); });
        return;
    }

    throw std::runtime_error(
        "FluidSimulationBuilder::addBoundaryConditions: unsupported: " + type);
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_SIMULATION_BUILDER_HPP
