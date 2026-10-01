#include "constraint_builder.h"

#include "geometry_builder.h"
#include "recording_builder.h"
#include "simbody_system.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void ConstraintBuilder::addConstraintWithSimbody(
    SPHSimulation &sim, MainMethods &method_container, RealBody &real_body, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    auto &simbody_system = sph_system.getSimbodySystem();
    Shape &shape = config_manager.getEntity<Shape>(real_body.Name());
    parseSimbodyMobilizedBody(config_manager, simbody_system, real_body, shape, config);

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

    auto &mobilized_body = simbody_system.getMobilizedBody<SimTK::MobilizedBody>(body_part.Name());
    auto &constraint = main_methods.template addStateDynamics<
        solid_dynamics::ConstraintBodyPartBySimBodyCK>(body_part, MBsystem, mobilized_body, integ);
    simulation_pipeline.insert_hook(
        SimulationHookPoint::PositionConstraint, [&]()
        {
                // (A) move the mobilized body to the target state at the current physical time
                Real t_target = time_stepper.getPhysicalTime();
                if (t_target > integ.getState().getTime())
                {
                    integ.stepTo(t_target);
                }
                // (B) carry out the constraint
                constraint.exec(); });

    return;
}
//=================================================================================================//
void ConstraintBuilder::parseSimbodyMobilizedBody(
    EntityManager &config_manager, SimbodySystem &simbody_system,
    RealBody &real_body, Shape &shape, const json &config)
{
    const std::string &mobilized_body_type = config.at("mobilized_body").get<std::string>();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    std::string simbody_name = simbody_system.createRigidBody(real_body, shape);

    if (mobilized_body_type == "planar")
    {
        std::string mobilized_planar = simbody_system.createFirstMobilizedPlanar(body_part.Name());
        Real omega_z = 2.0 * Pi * scaling_config.jsonToReal(config.at("angular_velocity"), "AngularVelocity");
        Vec2d velocity = scaling_config.jsonToVecd(config.at("velocity"), "Velocity");
        simbody_system.setUForMobilizedPlanar(body_part.Name(), velocity, omega_z);
    }

    if (mobilized_body_type == "pin")
    {
        SimTK::MobilizedBody::Pin &mobilized_body_pin =
            simbody_system.createMobilizedBody<SimTK::MobilizedBody::Pin>(
                body_part.Name(), matter.Ground(), body_part.getSimTKMassCenter(),
                simbody_body, body_part.getSimTKTransform());

        SimTK::State state = MBsystem.realizeTopology();
        // set the initial velocity of the mobilized body
        Real omega_z = 2.0 * Pi * scaling_config.jsonToReal(config.at("angular_velocity"), "AngularVelocity");
        mobilized_body_pin.setU(state, omega_z); // set the initial velocity of the mobilized body
        return state;
    }

    throw std::runtime_error(
        "ConstraintBuilder::addConstraint:simbody unsupported mobilized body type: " + mobilized_body_type);
}
//=================================================================================================//
} // namespace SPH
