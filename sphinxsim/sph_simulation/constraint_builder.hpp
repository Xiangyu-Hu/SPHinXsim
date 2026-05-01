#ifndef CONSTRAINT_BUILDER_HPP
#define CONSTRAINT_BUILDER_HPP

#include "constraint_builder.h"

#include "geometry_builder.h"
#include "io_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
void ConstraintBuilder::addConstraints(
    SPHSimulation &sim, MethodContainerType &method_container, const json &config)
{
    SPHSystem &sph_system = sim.getSPHSystem();
    for (const auto &constraint_config : config.at("body_constraints"))
    {
        const std::string body_name = constraint_config.at("body_name").get<std::string>();
        RealBody &real_body = sph_system.getBodyByName<RealBody>(body_name);
        addConstraint(sim, method_container, real_body, constraint_config);
    }
}
//=================================================================================================//
template <class MethodContainerType>
void ConstraintBuilder::addConstraint(
    SPHSimulation &sim, MethodContainerType &method_container, RealBody &real_body, const json &config)
{
    EntityManager &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();
    RestartConfig &restart_config = config_manager.getEntity<RestartConfig>("RestartConfig");
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();

    const std::string type = config.at("type").get<std::string>();

    if (type == "fixed")
    {
        auto &constraint = method_container.addParticleDynamicsGroup();
        if (config.contains("region"))
        {
            Shape &shape = config_manager.getEntity<Shape>(config.at("region").get<std::string>());
            BodyPartByParticle &body_part = real_body.addBodyPart<BodyRegionByParticle>(shape);

            constraint.add(&method_container.template addStateDynamics<
                            ConstantConstraintCK, Vecd>(body_part, "Velocity", Vecd::Zero()));
        }
        else
        {
            constraint.add(&method_container.template addStateDynamics<
                            ConstantConstraintCK, Vecd>(real_body, "Velocity", Vecd::Zero()));
        }

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            { constraint.exec(); });
        return;
    }

    if (type == "simbody")
    {
        SimTK::MultibodySystem &MBsystem = *config_manager.emplaceEntity<
            SimTK::MultibodySystem>("SimbodyMultibodySystem");
        SimTK::SimbodyMatterSubsystem &matter = *config_manager.emplaceEntity<
            SimTK::SimbodyMatterSubsystem>("SimbodyMatterSubsystem", MBsystem);
        Shape &shape = config_manager.getEntity<Shape>(real_body.getName());
        SolidBodyPartForSimbody &body_part = real_body.addBodyPart<SolidBodyPartForSimbody>(shape);
        SimTK::Body::Rigid &simbody_body = *config_manager.emplaceEntity<
            SimTK::Body::Rigid>(body_part.getName(), *body_part.body_part_mass_properties_);

        const std::string mobilized_body_type = config.at("mobilized_body").get<std::string>();

        if (mobilized_body_type == "planar")
        {
            SimTK::MobilizedBody::Planar &mobilized_body =
                *config_manager.emplaceEntity<SimTK::MobilizedBody::Planar>(
                    "SimbodyMobilizedBody",
                    matter.Ground(), SimTK::Transform(SimTKVec3(0.0, 0.0, 0.0)),
                    simbody_body, SimTK::Transform(SimTKVec3(0.0, 0.0, 0.0)));
            SimTK::RungeKuttaMersonIntegrator &integ =
                *config_manager.emplaceEntity<SimTK::RungeKuttaMersonIntegrator>(
                    "SimbodyIntegrator", MBsystem);
            MBsystem.realizeTopology();

            SimTK::State state = MBsystem.getDefaultState();
            // set the initial velocity of the mobilized body
            Real omega_z = 2.0 * Pi * scaling_config.jsonToReal(config.at("angular_velocity"), "AngularVelocity");
            Vec3d velocity = upgradeToVec3d(scaling_config.jsonToVecd(config.at("velocity"), "Velocity"));
            SimTK::Vec3 u_cmd = SimTK::Vec3(omega_z, velocity[0], velocity[1]);
            mobilized_body.setU(state, u_cmd); // set the initial velocity of the mobilized body

            if (restart_config.enabled_)
            {
                SPH::SimbodyStateEngine &state_engine = *config_manager.emplaceEntity<
                    SPH::SimbodyStateEngine>("SimbodyStateEngine", MBsystem);

                simulation_pipeline.insert_hook(
                    SimulationHookPoint::ExtraOutput, [&]()
                    { 
                        UnsignedInt iteration_step = time_stepper.getIterationStep();
                        if (iteration_step % restart_config.save_interval_ == 0)
                        {
                            state_engine.writeStateToXml(iteration_step, integ);
                        } });

                if (restart_config.restore_step_ != 0)
                {
                    state_engine.readStateFromXml(restart_config.restore_step_, state);
                    MBsystem.realize(state);
                }
            }
            integ.initialize(state);

            auto &constraint = method_container.template addStateDynamics<
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
        throw std::runtime_error(
            "ConstraintBuilder::addConstraint:simbody unsupported mobilized body type: " + mobilized_body_type);
    }

    throw std::runtime_error(
        "ConstraintBuilder::ConstraintBuilder: unsupported: " + type);
}
//=================================================================================================//
} // namespace SPH
#endif // CONSTRAINT_BUILDER_HPP
