#ifndef SPH_SIMULATION_HPP
#define SPH_SIMULATION_HPP

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
void SPHSimulation::addFluidBoundaryConditions(
    MethodContainerType &method_container, EntityManager &entity_manager, const json &config)
{
    const std::string body_name = config.at("body_name").get<std::string>();
    FluidBody &fluid_body = entity_manager.getEntityByName<FluidBody>(body_name);
    const std::string type = config.at("type").get<std::string>();

    if (type == "emitter")
    { // must be aligned box for emitter
        int alignment_axis = config.at("alignment_axis").get<int>();
        Vecd half_size = jsonToVecd(config.at("half_size"));
        Vecd translation = jsonToVecd(config.at("translation"));
#ifdef SPHINXSYS_2D
        Rotation rotation(config.at("rotation_angle").get<Real>());
#else
        Rotation rotation(config.at("rotation_angle").get<Real>(),
                            jsonToVecd(config.at("rotation_axis")));
#endif
        auto &emitter = fluid_body.addBodyPart<AlignedBoxByParticle>(
            AlignedBox(alignment_axis, Transform(rotation, translation), half_size));
        emitter.writeShapeProxy();
        auto &inflow_condition = method_container.template addStateDynamics<
            fluid_dynamics::EmitterInflowConditionCK, ConstantInflowVelocity>(
            emitter, config.at("inflow_speed").get<Real>());
        auto &injection = method_container.template addStateDynamics<
            fluid_dynamics::EmitterInflowInjectionCK>(emitter);

        simulation_pipeline_.insert_hook(
            SimulationHookPoint::BoundaryConditions, [&]()
            { inflow_condition.exec(); });

        simulation_pipeline_.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { injection.exec(); });
        return;
    }

    throw std::runtime_error("SPHSimulation::addBodyPart: unsupported boundary condition type: " + type);
}
//=================================================================================================//
} // namespace SPH
#endif // SPH_SIMULATION_HPP
