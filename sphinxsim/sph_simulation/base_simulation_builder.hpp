#ifndef BASE_SIMULATION_BUILDER_HPP
#define BASE_SIMULATION_BUILDER_HPP

#include "base_simulation_builder.h"

#include "material_builder.h"
#include "io_builder.hpp"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
void SimulationBuilder::buildExternalForceIfPresent(
    SPHSimulation &sim, MethodContainerType &main_methods, SPHBody &sph_body, const json &config)
{
    auto &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto &initialization_pipeline = sim.getInitializationPipeline();

    if (config.contains("gravity"))
    {
        auto &constant_gravity =
            main_methods.template addStateDynamics<GravityForceCK<Gravity>>(
                sph_body, Gravity(scaling_config.jsonToVecd(config.at("gravity"), "Acceleration")));

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { constant_gravity.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH
#endif // BASE_SIMULATION_BUILDER_HPP
