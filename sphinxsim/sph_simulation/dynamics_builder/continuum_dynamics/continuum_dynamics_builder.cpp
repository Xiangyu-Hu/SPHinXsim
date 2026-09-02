#include "continuum_dynamics_builder.h"

#include "all_continuum_dynamics_ck.h"
#include "constraint_builder.h"
#include "density_regularization.h"
#include "recording_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
BaseDynamics<void> &ContinuumDynamicsBuilder::addAdvectionStepSetup(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &advection_step_setup = main_methods.addParticleDynamicsGroup();

    for (const auto &cb : continuum_bodies_config)
    {
        auto &continuum_body = sph_system.getBodyByName<RealBody>(cb->name_);
        advection_step_setup.add(&main_methods.addStateDynamics<fluid_dynamics::AdvectionStepSetup>(
            continuum_body));
    }
    return advection_step_setup;
}
//=================================================================================================//
BaseDynamics<void> &ContinuumDynamicsBuilder::addUpdateParticlePosition(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &update_particle_position = main_methods.addParticleDynamicsGroup();

    for (const auto &cb : continuum_bodies_config)
    {
        auto &continuum_body = sph_system.getBodyByName<RealBody>(cb->name_);
        update_particle_position.add(
            &main_methods.addStateDynamics<fluid_dynamics::UpdateParticlePosition>(continuum_body));
    }
    return update_particle_position;
}
//=================================================================================================//
BaseDynamics<Real> &ContinuumDynamicsBuilder::addAdvectionTimeStep(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &advection_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &continuum_solver_parameters = config_manager.getEntity<ContinuumSolverParameters>(
        "ContinuumSolverParameters");

    for (const auto &cb : continuum_bodies_config)
    {
        auto &continuum_body = sph_system.getBodyByName<RealBody>(cb->name_);
        advection_time_step.add(&main_methods.addReduceDynamics<fluid_dynamics::AdvectionTimeStepCK>(
            continuum_body, Real(1), continuum_solver_parameters.advection_cfl_));
    }
    return advection_time_step;
}
//=================================================================================================//
BaseDynamics<Real> &ContinuumDynamicsBuilder::addAcousticTimeStep(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &acoustic_time_step = main_methods.addReduceDynamicsGroup<ReduceMin<Real>>();
    auto &continuum_solver_parameters = config_manager.getEntity<ContinuumSolverParameters>(
        "ContinuumSolverParameters");

    for (const auto &cb : continuum_bodies_config)
    {
        auto &continuum_body = sph_system.getBodyByName<RealBody>(cb->name_);
        acoustic_time_step.add(
            &main_methods.addReduceDynamics<
                fluid_dynamics::AcousticTimeStepCK<WeaklyCompressibleFluid>>(
                continuum_body, continuum_solver_parameters.acoustic_cfl_));
    }
    return acoustic_time_step;
}
//=================================================================================================//
BaseDynamics<void> &ContinuumDynamicsBuilder::addLinearCorrectionMatrix(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &linear_correction_matrix = main_methods.addParticleDynamicsGroup();
    auto &continuum_solver_parameters = config_manager.getEntity<
        ContinuumSolverParameters>("ContinuumSolverParameters");

    for (const auto &cb : continuum_bodies_config)
    {
        std::string body_name = cb->name_;
        if (!config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
        {
            auto &inner_relation = sph_system.getRelationByName<Inner<Relation<RealBody>>>(body_name);
            linear_correction_matrix.add(
                &main_methods.template addInteractionDynamicsWithUpdate<LinearCorrectionMatrix>(
                    inner_relation, continuum_solver_parameters.linear_correction_matrix_coeff_));
        }
    }
    return linear_correction_matrix;
}
//=================================================================================================//
void ContinuumDynamicsBuilder::buildShearForceIntegrationIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &continuum_shear_force = main_methods.addParticleDynamicsGroup();

    for (const auto &cb : continuum_bodies_config)
    {
        std::string body_name = cb->name_;
        if (!config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
        {
            auto &inner_relation = sph_system.getRelationByName<Inner<Relation<RealBody>>>(body_name);
            continuum_shear_force.add(&main_methods.template addInteractionDynamics<
                                       LinearGradient, Vecd>(inner_relation, "Velocity"));

            auto &continuum_solver_parameters = config_manager.getEntity<
                ContinuumSolverParameters>("ContinuumSolverParameters");

            if (config_manager.hasEntity<J2Plasticity>(body_name + "J2Plasticity"))
            {
                continuum_shear_force
                    .add(&main_methods.template addInteractionDynamicsOneLevel<
                          continuum_dynamics::InelasticShearIntegration, J2Plasticity>(
                        inner_relation, continuum_solver_parameters.hourglass_factor_,
                        continuum_solver_parameters.shear_stress_damping_));
            }
        }
    }

    if (continuum_shear_force.hasDynamics())
    {
        auto &time_stepper = sim.getSPHSolver().getTimeStepper();
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::BeforeMainPhysicalTimeStep, [&]()
            {   Real dt = time_stepper.getGlobalTimeStepSize(); 
                continuum_shear_force.exec(dt); });
    }
}
//=================================================================================================//
void ContinuumDynamicsBuilder::buildContactRepulsionIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &contact_repulsion_factor = main_methods.addParticleDynamicsGroup();
    auto &contact_repulsion_force = main_methods.addParticleDynamicsGroup();
    auto &continuum_solver_parameters = config_manager.getEntity<
        ContinuumSolverParameters>("ContinuumSolverParameters");

    for (const auto &cb_src : continuum_bodies_config)
    {
        std::string body_name = cb_src->name_;
        if (!config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
        {
            for (const auto &cb_tgt : continuum_bodies_config)
            {
                std::string tgt_body_name = cb_tgt->name_;
                if (body_name != tgt_body_name &&
                    !config_manager.hasEntity<PlasticContinuum>(tgt_body_name + "PlasticContinuum"))
                {
                    std::string relation_name = body_name + tgt_body_name;
                    auto &contact_relation =
                        sph_system.getRelationByName<Contact<Relation<RealBody, RealBody>>>(relation_name);
                    contact_repulsion_factor.add(
                        &main_methods.template addInteractionDynamics<solid_dynamics::RepulsionFactor>(
                            contact_relation));
                    contact_repulsion_force.add(
                        &main_methods.template addInteractionDynamicsWithUpdate<solid_dynamics::RepulsionForceCK>(
                            contact_relation, continuum_solver_parameters.contact_numerical_damping_));
                }
            }

            if (config_manager.hasEntity<SPHBodiesConfig>("SolidBodiesConfig"))
            {
                auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
                for (const auto &sb_tgt : solid_bodies_config)
                {
                    std::string relation_name = body_name + sb_tgt->name_;
                    auto &contact_relation = sph_system.getRelationByName<
                        Contact<Relation<RealBody, SolidBody>>>(relation_name);
                    
                    contact_repulsion_factor.add(
                        &main_methods.template addInteractionDynamics<solid_dynamics::RepulsionFactor>(
                            contact_relation));


                    contact_repulsion_force.add(
                        &main_methods.template addInteractionDynamicsWithUpdate<
                            solid_dynamics::RepulsionForceCK, Wall>(
                            contact_relation, continuum_solver_parameters.contact_numerical_damping_));
                }
            }
        }
    }

    if (contact_repulsion_factor.hasDynamics())
    {
        auto &initialization_pipeline = sim.getInitializationPipeline();
        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialAfterLinearCorrectionMatrix, [&]()
            { contact_repulsion_factor.exec(); });

        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::BeforeMainPhysicalTimeStep, [&]()
            { contact_repulsion_force.exec(); });
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { contact_repulsion_factor.exec(); });
    }
}
//=================================================================================================//
void ContinuumDynamicsBuilder::buildDensityRegularizationIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &density_regularization = main_methods.addParticleDynamicsGroup();

    for (const auto &cb_src : continuum_bodies_config)
    {
        std::string body_name = cb_src->name_;
        if (!config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
        {
            auto &inner_relation = sph_system.getRelationByName<Inner<Relation<RealBody>>>(body_name);
            density_regularization.add(
                &main_methods.template addInteractionDynamics<fluid_dynamics::CompressionSummation>(
                    inner_relation));
            auto &continuum_body = sph_system.getBodyByName<RealBody>(body_name);
            density_regularization.add(
                &main_methods.template addStateDynamics<
                    fluid_dynamics::DensityRegularization, WeaklyCompressibleFluid, fluid_dynamics::Failure>(
                    continuum_body));
        }
    }

    if (density_regularization.hasDynamics())
    {
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterUpdateConfiguration, [&]()
            { density_regularization.exec(); });
    }
}
//=================================================================================================//
void ContinuumDynamicsBuilder::buildStressDiffusionIfPresent(
    SPHSimulation &sim, MainMethods &main_methods)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &continuum_bodies_config = config_manager.getEntity<SPHBodiesConfig>(
        "ContinuumBodiesConfig");
    auto &stress_diffusion = main_methods.addParticleDynamicsGroup();

    for (const auto &cb_src : continuum_bodies_config)
    {
        std::string body_name = cb_src->name_;
        if (config_manager.hasEntity<PlasticContinuum>(body_name + "PlasticContinuum"))
        {
            auto &inner_relation = sph_system.getRelationByName<Inner<Relation<RealBody>>>(body_name);
            stress_diffusion.add(
                &main_methods.template addInteractionDynamics<
                    continuum_dynamics::StressDiffusionCK>(inner_relation));

            auto &continuum_body = sph_system.getBodyByName<RealBody>(body_name);
            auto &body_state_recorder = RecordingBuilder::getBodyStatesRecording(config_manager);
            body_state_recorder.template addDerivedVariableToWrite<
                continuum_dynamics::VerticalStressCK>(continuum_body);
            body_state_recorder.template addDerivedVariableToWrite<
                continuum_dynamics::AccDeviatoricPlasticStrainCK>(continuum_body);
        }
    }

    if (stress_diffusion.hasDynamics())
    {
        auto &time_stepper = sim.getSPHSolver().getTimeStepper();
        auto &simulation_pipeline = sim.getSimulationPipeline();
        simulation_pipeline.insert_hook(
            SimulationHookPoint::BeforeMainPhysicalTimeStep, [&]()
            {   Real dt = time_stepper.getGlobalTimeStepSize();
                stress_diffusion.exec(dt); });
    }
}
//=================================================================================================//
} // namespace SPH
