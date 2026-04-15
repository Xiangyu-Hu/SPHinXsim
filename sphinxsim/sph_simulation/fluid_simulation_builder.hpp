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
    EntityManager &entity_manager = sim.getEntityManager();
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();

    const std::string body_name = config.at("body_name").get<std::string>();
    FluidBody &fluid_body = entity_manager.getEntityByName<FluidBody>(body_name);
    const std::string type = config.at("type").get<std::string>();

    if (type == "emitter")
    { // must be aligned box for emitter
        int alignment_axis = config.at("alignment_axis").get<int>();
        TransformGeometryBox box = GeometryBuilder::parseBox(config);
        auto &emitter = fluid_body.addBodyPart<AlignedBoxByParticle>(AlignedBox(alignment_axis, box));
        emitter.writeAlignedBoxToVtp();
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
