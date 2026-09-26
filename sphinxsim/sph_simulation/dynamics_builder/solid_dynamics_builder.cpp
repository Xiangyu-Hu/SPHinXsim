#include "solid_dynamics_builder.hpp"

#include "material_builder.h"
#include "sph_simulation.h"
#include "structure_surface_motion.h"

namespace SPH
{
//=================================================================================================//
using namespace solid_dynamics;
//=================================================================================================//
void SolidDynamicsBuilder::buildSolidsDynamicsIfPresentInFluid(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
    auto &time_stepper = sim.getSPHSolver().getTimeStepper();
    auto &solid_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &initialize_displacement = main_methods.addParticleDynamicsGroup();
    auto &update_average_velocity = main_methods.addParticleDynamicsGroup();
    auto &elastic_correction_matrix = main_methods.addParticleDynamicsGroup();
    auto &solid_relaxation_1st_half = main_methods.addParticleDynamicsGroup();
    auto &solid_relaxation_2nd_half = main_methods.addParticleDynamicsGroup();
    auto &elastic_normal_direction = main_methods.addParticleDynamicsGroup();

    for (const auto &solid_config : solid_bodies_config)
    {
        std::string body_name = solid_config->name_;
        auto &solid_body = sph_system.getBodyByName<SolidBody>(body_name);

        if (solid_body.isMatterMaterial<ElasticSolid>())
        {
            initialize_displacement.add(
                &main_methods.addStateDynamics<
                    FSI::InitializeDisplacementCK>(solid_body));

            auto &inner_relation = sph_system.getRelationByName<
                Inner<Relation<SolidBody>>>(body_name);
            elastic_correction_matrix.add(
                &main_methods.addInteractionDynamics<
                    LinearCorrectionMatrix, WithUpdate>(inner_relation));

            buildSolidRelaxation1stHalf(
                config_manager, solid_relaxation_1st_half, main_methods, inner_relation);

            solid_relaxation_2nd_half.add(
                &main_methods.template addInteractionDynamicsOneLevel<
                    StructureIntegration2ndHalf>(inner_relation));

            solid_time_step.add(
                &main_methods.addReduceDynamics<AcousticTimeStepCK>(solid_body));

            update_average_velocity.add(
                &main_methods.addStateDynamics<
                    FSI::UpdateAverageVelocityAndAccelerationCK>(solid_body));

            elastic_normal_direction.add(
                &main_methods.addStateDynamics<
                    UpdateElasticNormalDirectionCK>(solid_body));
        }
    }

    if (initialize_displacement.hasDynamics())
    {
        auto &solid_relaxation = main_methods.addParticleDynamicsGroup();
        solid_relaxation.add(&solid_relaxation_1st_half).add(&solid_relaxation_2nd_half);

        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { elastic_correction_matrix.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { initialize_displacement.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { 
          Real dt = time_stepper.getGlobalTimeStepSize();
          time_stepper.integrateMatchedTimeInterval(solid_relaxation, dt, solid_time_step); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { update_average_velocity.exec(time_stepper.getGlobalTimeStepSize()); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { elastic_normal_direction.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH