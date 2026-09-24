/* ------------------------------------------------------------------------- *
 *                                SPHinXsys                                  *
 * ------------------------------------------------------------------------- *
 * SPHinXsys (pronunciation: s'finksis) is an acronym from Smoothed Particle *
 * Hydrodynamics for industrial compleX systems. It provides C++ APIs for    *
 * physical accurate simulation and aims to model coupled industrial dynamic *
 * systems including fluid, solid, multi-body dynamics and beyond with SPH   *
 * (smoothed particle hydrodynamics), a meshless computational method using  *
 * particle discretization.                                                  *
 *                                                                           *
 * SPHinXsys is partially funded by German Research Foundation               *
 * (Deutsche Forschungsgemeinschaft) DFG HU1527/6-1, HU1527/10-1,            *
 *  HU1527/12-1 and HU1527/12-4.                                             *
 *                                                                           *
 * Portions copyright (c) 2017-2025 Technical University of Munich and       *
 * the authors' affiliations.                                                *
 *                                                                           *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may   *
 * not use this file except in compliance with the License. You may obtain a *
 * copy of the License at http://www.apache.org/licenses/LICENSE-2.0.        *
 *                                                                           *
 * ------------------------------------------------------------------------- */
/**
 * @file    solid_dynamics_builder.hpp
 * @brief   Builds the elastic solid stress relaxation group and drives it
 *          as a sub loop inside each coupling interval.
 * @author  Pruthvik Arasikere Mallikarjuna and Xiangyu Hu
 */

#ifndef SOLID_DYNAMICS_BUILDER_HPP
#define SOLID_DYNAMICS_BUILDER_HPP

#include "solid_dynamics_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

#include "composite_solid.h"
#include "structure_surface_motion.h"
#include "traveling_wave_active_strain.h"

namespace SPH
{
//=================================================================================================//
template <class MaterialType, class MethodContainerType, class InnerRelationType>
auto &SolidDynamicsBuilder::buildSolidDynamics(
    SPHSimulation &sim, MethodContainerType &method_container,
    InnerRelationType &inner_relation,
    std::function<void()> pre_substep_hook)
{
    auto &sph_system = sim.getSPHSystem();
    auto &time_stepper = sim.getSPHSolver().getTimeStepper();

    std::string body_name = inner_relation.getSPHBody().Name();
    RealBody &real_body = sph_system.getBodyByName<RealBody>(body_name);

    // Solid stress relaxation reads these kinematic variables; register them
    // before the stress steps so they exist when the steps are constructed
    BaseParticles &solid_particles = real_body.getBaseParticles();
    solid_particles.registerStateVariable<Vecd>("Velocity");
    solid_particles.registerStateVariable<Vecd>("Force");
    solid_particles.registerStateVariable<Vecd>("ForcePrior");
    solid_particles.addEvolvingVariable<Vecd>("Velocity");

    auto &correction_matrix =
    method_container.template addInteractionDynamics<LinearCorrectionMatrix, WithUpdate>(inner_relation);

    // the solid picks its own step from the wave speed; this drives the sub-loop
    auto &solid_time_step =
        method_container.template addReduceDynamics<solid_dynamics::AcousticTimeStepCK>(real_body);

    // stress relaxation runs as damping, then the two PK2 half steps, in this order
    auto &numerical_damping =
        method_container.template addInteractionDynamicsWithUpdate<
            solid_dynamics::StructureNumericalDamping, MaterialType>(inner_relation);
    auto &stress_first_half =
        method_container.template addInteractionDynamicsOneLevel<
            solid_dynamics::StructureIntegration1stHalfPK2, MaterialType>(inner_relation);
    auto &stress_second_half =
        method_container.template addInteractionDynamicsOneLevel<
            solid_dynamics::StructureIntegration2ndHalf>(inner_relation);

    auto &solid_relaxation = method_container.addParticleDynamicsGroup();
    solid_relaxation.add(&numerical_damping).add(&stress_first_half).add(&stress_second_half);

    // fill each coupling interval with as many solid sub-steps as it takes
    auto &simulation_pipeline = sim.getSimulationPipeline();
    simulation_pipeline.insert_hook(
        SimulationHookPoint::CouplingSynchronization, [&, pre_substep_hook]()
        {
            Real dt = time_stepper.getGlobalTimeStepSize();
            if (!(dt > 0.0))
            {
                throw std::runtime_error(
                    "SolidDynamicsBuilder: coupling interval is not a positive number.");
            }
            Real solid_dt = solid_time_step.exec();
            if (!(solid_dt > 0.0))
            {
                throw std::runtime_error(
                    "SolidDynamicsBuilder: structure time step is not a positive number.");
            }
            // Matches SYCL: re-run the pre-substep hook (active strain) before
            // every solid sub-step, not once for the whole coupling interval.
            time_stepper.integrateMatchedTimeInterval(
                dt, solid_time_step, [&](Real dt_s)
                {
                    if (pre_substep_hook)
                    {
                        pre_substep_hook();
                    }
                    solid_relaxation.exec(dt_s);
                });
        });
    return correction_matrix;
}
//=================================================================================================//
inline void SolidDynamicsBuilder::buildCompositeSolidsIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");

    for (const auto &solid_config : config.at("solid_bodies"))
    {
        const std::string material_type =
            solid_config.at("material").at("type").get<std::string>();

        if (material_type != "composite_solid")
            continue;

        std::string body_name = solid_config.at("name").get<std::string>();
        RealBody &elastic_body = sph_system.getBodyByName<RealBody>(body_name);
        auto &elastic_inner =
            sph_system.getRelationByName<Inner<Relation<SolidBody>>>(body_name);

        auto &initialize_displacement =
            main_methods.addStateDynamics<InitializeDisplacementCK>(elastic_body);

        auto &update_average_velocity =
            main_methods.addStateDynamics<UpdateAverageVelocityAndAccelerationCK>(elastic_body);

        // Snapshot the surface before the structure advances.
        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { initialize_displacement.exec(); });

        const json &material_config = solid_config.at("material");
        std::function<void()> active_strain_pre_substep_hook = nullptr;
        if (material_config.contains("active_strain"))
        {
            const json &wave_config = material_config.at("active_strain");

            Vecd wave_center = Vecd::Zero();
            for (int k = 0; k != wave_center.size(); ++k)
            {
                wave_center[k] = scaling_config.jsonToReal(wave_config.at("center").at(k), "Length");
            }
            Real wave_span = scaling_config.jsonToReal(wave_config.at("region_span"), "Length");
            Real wave_core = scaling_config.jsonToReal(wave_config.at("core_thickness"), "Length");
            Real amplitude = wave_config.at("amplitude").get<Real>();
            Real frequency = wave_config.at("frequency").get<Real>();
            Real wavelength_factor = wave_config.at("wavelength_factor").get<Real>();
            Real start_time = wave_config.at("start_time").get<Real>();

            auto &active_strain = main_methods.addStateDynamics<TravelingWaveActiveStrain>(
                elastic_body, wave_center, wave_span, wave_core,
                amplitude, frequency, wavelength_factor, start_time);

            active_strain_pre_substep_hook = [&active_strain]()
            { active_strain.exec(); };
        }

        auto &elastic_correction_matrix =
            SolidDynamicsBuilder::buildSolidDynamics<CompositeSolidMaterial>(
                sim, main_methods, elastic_inner, active_strain_pre_substep_hook);

        // Recover the averaged surface motion the fluid sees over the interval.
        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::CouplingSynchronization, [&]()
            { update_average_velocity.exec(sim.getSPHSolver().getTimeStepper().getGlobalTimeStepSize()); });

        auto &elastic_normal_direction =
            main_methods.addStateDynamics<solid_dynamics::UpdateElasticNormalDirectionCK>(elastic_body);

        sim.getSimulationPipeline().insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { elastic_normal_direction.exec(); });

        sim.getInitializationPipeline().insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            {
                elastic_correction_matrix.exec();
                elastic_normal_direction.exec(); });
    }
}
//=================================================================================================//
} // namespace SPH
#endif // SOLID_DYNAMICS_BUILDER_HPP
