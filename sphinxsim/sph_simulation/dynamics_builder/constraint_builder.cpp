#include "constraint_builder.h"

#include "geometry_builder.h"
#include "recording_builder.h"
#include "simbody_system.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void ConstraintBuilder::buildConstraintsIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    if (!config.contains("body_constraints"))
        return;

    SPHSystem &sph_system = sim.getSPHSystem();
    for (const auto &constraint_config : config.at("body_constraints"))
    {
        const std::string body_name = constraint_config.at("body_name").get<std::string>();
        RealBody &real_body = sph_system.getBodyByName<RealBody>(body_name);
        addConstraint(sim, main_methods, real_body, constraint_config);
    }
}
//=================================================================================================//
void ConstraintBuilder::addConstraint(
    SPHSimulation &sim, MainMethods &main_methods, RealBody &real_body, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    EntityManager &config_manager = sim.getConfigManager();
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();

    auto &sph_body_config = config_manager.getEntity<SPHBodyConfig>(real_body.Name());
    if (sph_body_config.is_moving_ == false)
    {
        throw std::runtime_error(
            "ConstraintBuilder::ConstraintBuilder: constrained body must be moving: " + real_body.Name());
    };

    const std::string type = config.at("type").get<std::string>();

    if (type == "fixed")
    {
        auto &constraint = main_methods.addParticleDynamicsGroup();
        if (config.contains("region"))
        {
            auto &oriented_box = config_manager.getEntity<OrientedBox>(config.at("region").get<std::string>());
            auto &body_part = real_body.template addBodyPart<OrientedBoxByParticle>(oriented_box);
            constraint.add(&main_methods.template addStateDynamics<FixConstraintCK>(body_part));
        }
        else
        {
            constraint.add(&main_methods.template addStateDynamics<FixConstraintCK>(real_body));
        }

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            { constraint.exec(); });
        return;
    }

    if (type == "simbody")
    {
        addConstraintWithSimbody(sim, main_methods, real_body, config);
        return;
    }

    throw std::runtime_error(
        "ConstraintBuilder::ConstraintBuilder: unsupported: " + type);
}
//=================================================================================================//
} // namespace SPH
