#include "thermal_dynamics_builder.hpp"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void ThermalDynamicsBuilder::buildThermalDynamicsIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &thermal_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &all_runge_kutta_1st_stage = main_methods.addParticleDynamicsGroup();
    auto &all_runge_kutta_2nd_stage = main_methods.addParticleDynamicsGroup();

    auto &fluid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("FluidBodiesConfig");
    for (const auto &fb_src : fluid_bodies_config)
    {
        std::string body_name = fb_src->name_;
        auto &fluid_body = sph_system.getBodyByName<FluidBody>(body_name);
        if (config_manager.hasEntity<IsotropicDiffusion>(body_name + "ThermalDiffusion"))
        {
            auto &diffusion = config_manager.getEntity<IsotropicDiffusion>(body_name + "ThermalDiffusion");
            thermal_time_step.add(&main_methods.template addReturnDynamics<GetDiffusionTimeStepSize>(
                fluid_body, &diffusion));

            auto &inner_relation = sph_system.getRelationByName<Inner<Relation<FluidBody>>>(body_name);

            auto &runge_kutta_1st_stage =
                main_methods.template addInteractionDynamicsOneLevel<
                    DiffusionRelaxationCK, RungeKutta1stStage, IsotropicDiffusion, LinearCorrectionCK>(
                    inner_relation, &diffusion);
            auto &runge_kutta_2nd_stage =
                main_methods.template addInteractionDynamicsOneLevel<
                    DiffusionRelaxationCK, RungeKutta2ndStage, IsotropicDiffusion, LinearCorrectionCK>(
                    inner_relation, &diffusion);

            auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
            for (const auto &sb_tgt : solid_bodies_config)
            {
                std::string target_body_name = sb_tgt->name_;
                if (config_manager.hasEntity<ThermalBoundaryConfig>(target_body_name))
                {
                    auto &bd_config = config_manager.getEntity<ThermalBoundaryConfig>(target_body_name);
                    auto &contact_relation = sph_system.getRelationByName<
                        Contact<Relation<FluidBody, SolidBody>>>(body_name + target_body_name);

                    buildThermalBoundaryCondition(
                        bd_config.boundary_type, runge_kutta_1st_stage, diffusion, contact_relation);
                    buildThermalBoundaryCondition(
                        bd_config.boundary_type, runge_kutta_2nd_stage, diffusion, contact_relation);
                }
            }

            all_runge_kutta_1st_stage.add(&runge_kutta_1st_stage);
            all_runge_kutta_2nd_stage.add(&runge_kutta_2nd_stage);
        }
    }

    auto &runge_kutta = main_methods.addParticleDynamicsGroup();
    runge_kutta.add(&all_runge_kutta_1st_stage).add(&all_runge_kutta_2nd_stage);
    auto &time_stepper = sim.getSPHSolver().getTimeStepper();
    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::CouplingSynchronization, [&]()
        { 
          Real dt = time_stepper.getGlobalTimeStepSize();
          time_stepper.integrateMatchedTimeInterval(runge_kutta, dt, thermal_time_step); });
}
//=================================================================================================//
} // namespace SPH
