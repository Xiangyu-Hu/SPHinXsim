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
        auto &simbody_system = sph_system.getSimbodySystem();
        Shape &shape = config_manager.getEntity<Shape>(real_body.Name());
        SolidBodyPartForSimbody &body_part = real_body.addBodyPart<SolidBodyPartForSimbody>(shape);
        parseSimbodyMobilizedBody(config_manager, simbody_system, body_part, config);

        if (config_manager.hasEntity<RestartConfig>("RestartConfig"))
        {
            auto &restart_config = config_manager.getEntity<RestartConfig>("RestartConfig");

            simulation_pipeline.insert_hook(
                SimulationHookPoint::ExtraOutput, [&]()
                { 
                        UnsignedInt iteration_step = time_stepper.getIterationStep();
                        if (iteration_step % restart_config.save_interval_ == 0)
                        {
                            simbody_system.writeStateToXml(iteration_step);
                        } });

            if (restart_config.restore_step_ != 0)
            {
                simbody_system.readStateFromXml(restart_config.restore_step_);
            }
        }

        simbody_system.realizeState();
        simbody_system.initializeStateForIntegrator();
        simbody_system.checkSimbodyState(shape.Name());

        auto &constraint = main_methods.template addStateDynamics<
            solid_dynamics::ConstraintBodyPartBySimBodyCK>(body_part, simbody_system);
        simulation_pipeline.insert_hook(
            SimulationHookPoint::PositionConstraint, [&]()
            {
                // (A) move the mobilized body to the target state at the current physical time
                Real t_target = time_stepper.getPhysicalTime();
                if (t_target > simbody_system.getSimbodySystemTime())
                {
                    simbody_system.stepSimbodySystemTo(t_target);
                }
                // (B) carry out the constraint
                constraint.exec(); });
        return;
    }

    throw std::runtime_error(
        "ConstraintBuilder::ConstraintBuilder: unsupported: " + type);
}
//=================================================================================================//
void ConstraintBuilder::parseSimbodyMobilizedBody(
    EntityManager &config_manager, SimbodySystem &simbody_system,
    SolidBodyPartForSimbody &body_part, const json &config)
{
    const std::string &mobilized_body_type = config.at("mobilized_body").get<std::string>();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    std::string simbody_name = simbody_system.createRigidBody(real_body, shape);

    if (mobilized_body_type == "planar")
    {
        std::string mobilized_planar = simbody_system.createFirstMobilizedPlanar(body_part);
        Real omega_z = 2.0 * Pi * scaling_config.jsonToReal(config.at("angular_velocity"), "AngularVelocity");
        Vec2d velocity = scaling_config.jsonToVecd(config.at("velocity"), "Velocity");
        simbody_system.setUForMobilizedPlanar(body_part.Name(), velocity, omega_z);
        return;
    }

    if (mobilized_body_type == "pin")
    {
        std::string mobilized_pin = simbody_system.createFirstMobilizedPin(body_part);
        Real omega_z = 2.0 * Pi * scaling_config.jsonToReal(config.at("angular_velocity"), "AngularVelocity");
        simbody_system.setUForMobilizedPin(omega_z);
        return;
    }

    throw std::runtime_error(
        "ConstraintBuilder::addConstraint:simbody unsupported mobilized body type: " + mobilized_body_type);
}
//=================================================================================================//
} // namespace SPH
