#include "fluid_dynamics_builder.hpp"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
void FluidDynamicsBuilder::buildBoundaryConditionsIfPresent(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    if (config.contains("fluid_boundary_conditions"))
    {
        for (const auto &bd : config.at("fluid_boundary_conditions"))
        {
            addBoundaryCondition(sim, main_methods, bd);
        }
    }
}
//=================================================================================================//
void FluidDynamicsBuilder::addBoundaryCondition(
    SPHSimulation &sim, MainMethods &main_methods, const json &config)
{
    StagePipeline<InitializationHookPoint> &initialization_pipeline = sim.getInitializationPipeline();
    StagePipeline<SimulationHookPoint> &simulation_pipeline = sim.getSimulationPipeline();
    EntityManager &config_manager = sim.getConfigManager();
    TimeStepper &time_stepper = sim.getSPHSolver().getTimeStepper();
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    const std::string body_name = config.at("body_name").get<std::string>();
    FluidBody &fluid_body = sim.getSPHSystem().getBodyByName<FluidBody>(body_name);
    const std::string oriented_box_name = config.at("oriented_box").get<std::string>();
    OrientedBox &oriented_box = config_manager.getEntity<OrientedBox>(oriented_box_name);
    const std::string type = config.at("type").get<std::string>();

    if (type == "emitter")
    { // must be oriented box for emitter
        auto &emitter = fluid_body.addBodyPart<OrientedBoxByParticle>(oriented_box);
        auto &inflow_condition = main_methods.addParticleDynamicsGroup();
        inflow_condition.add(&main_methods.template addStateDynamics<
                              EmitterInflowConditionCK, ConstantInflowSpeed>(
            emitter, scaling_config.jsonToReal(config.at("inflow_speed"), "Speed")));

        auto &fix_constraint = main_methods.template addStateDynamics<
            FixConstraintCK>(emitter);
        auto &injection = main_methods.template addStateDynamics<
            EmitterInflowInjectionCK>(emitter);

        fluid_solver_config.emitter_on_ = true; // enable emitter
        if (config.contains("on_schedule"))
        {
            SimulationBuilder::parseScheduledEvents(
                sim, config.at("on_schedule"), fluid_solver_config.emitter_on_);
        }

        if (config_manager.hasEntity<WeaklyCompressibleMultiPhase>(
                body_name + "WeaklyCompressibleMultiPhase"))
        {
            auto &mixture = config_manager.getEntity<WeaklyCompressibleMultiPhase>(
                body_name + "WeaklyCompressibleMultiPhase");

            if (config.contains("multi_species_phases"))
            {
                for (const auto &phase : config.at("multi_species_phases"))
                {
                    std::string phase_name = phase.at("phase_name").get<std::string>();
                    auto &multi_species_phase = mixture.getMultiSpeciesPhaseByName(phase_name);
                    StdVec<Real> mass_fractions = MaterialBuilder::parseMixtureFractions(
                        scaling_config, phase.at("mass_fractions"));

                    inflow_condition.add(
                        &main_methods.template addStateDynamics<
                            VariableAssignment,
                            ConstantMixtureFraction<WeaklyCompressibleMultiSpecies>>(
                            emitter, multi_species_phase, mass_fractions));
                }
            }

            if (config.contains("volume_fractions"))
            {
                StdVec<Real> volume_fractions = MaterialBuilder::parseMixtureFractions(
                    scaling_config, config.at("volume_fractions"));
                inflow_condition.add(
                    &main_methods.template addStateDynamics<
                        VariableAssignment,
                        ConstantMixtureFraction<WeaklyCompressibleMultiPhase>>(
                        emitter, mixture, volume_fractions));
                inflow_condition.add(
                    &main_methods.template addStateDynamics<
                        VariableAssignment,
                        UpdateReferenceDensity<WeaklyCompressibleMultiPhase>>(
                        emitter, mixture));
            }
        }

        if (config_manager.hasEntity<WeaklyCompressibleMultiSpecies>(
                body_name + "WeaklyCompressibleMultiSpecies"))
        {
            auto &mixture = config_manager.getEntity<WeaklyCompressibleMultiSpecies>(
                body_name + "WeaklyCompressibleMultiSpecies");
            if (config.contains("mass_fractions"))
            {
                StdVec<Real> mass_fractions = MaterialBuilder::parseMixtureFractions(
                    scaling_config, config.at("mass_fractions"));
                inflow_condition.add(
                    &main_methods.template addStateDynamics<
                        VariableAssignment,
                        ConstantMixtureFraction<WeaklyCompressibleMultiSpecies>>(
                        emitter, mixture, mass_fractions));

                inflow_condition.add(
                    &main_methods.template addStateDynamics<
                        VariableAssignment,
                        UpdateReferenceDensity<WeaklyCompressibleMultiSpecies>>(
                        emitter, mixture));
            }
        }

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { if(fluid_solver_config.emitter_on_)
                  inflow_condition.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            { if(fluid_solver_config.emitter_on_)
                  inflow_condition.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::PositionConstraint, [&]()
            { if(!fluid_solver_config.emitter_on_)
                    fix_constraint.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { if(fluid_solver_config.emitter_on_)
                injection.exec(); });

        return;
    }

    if (type == "bi_directional")
    {
        if (fluid_solver_config.surface_type_ != "open_boundary")
        {
            std::cout << "\n------------------------------------------------------------" << std::endl;
            std::cout << "FluidDynamicsBuilder::buildBoundaryConditionsIfPresent:" << std::endl;
            std::cout << "Error: bi_directional condition at OrientBox '" << oriented_box_name
                      << "' only works for open boundary flow!" << std::endl;
            std::cout << "------------------------------------------------------------" << std::endl;
        }

        auto &oriented_box_by_cell = fluid_body.addBodyPart<OrientedBoxByCell>(oriented_box);
        auto &bi_directional_bd = createBiDirectionBoundary(
            oriented_box_by_cell, config_manager, main_methods, config);

        auto &supplementary_conditions = main_methods.addParticleDynamicsGroup();
        if (config_manager.hasEntity<WeaklyCompressibleMultiSpecies>(
                body_name + "WeaklyCompressibleMultiSpecies"))
        {
            auto &mixture = config_manager.getEntity<WeaklyCompressibleMultiSpecies>(
                body_name + "WeaklyCompressibleMultiSpecies");
            if (config.contains("mass_fractions"))
            {
                StdVec<Real> mass_fractions = MaterialBuilder::parseMixtureFractions(
                    scaling_config, config.at("mass_fractions"));

                supplementary_conditions.add(
                    &main_methods.template addStateDynamics<
                        SupplementaryCondition<ConstantMixtureFraction<WeaklyCompressibleMultiSpecies>>>(
                        oriented_box_by_cell, mixture, mass_fractions));

                supplementary_conditions.add(
                    &main_methods.template addStateDynamics<
                        SupplementaryCondition<UpdateReferenceDensity<WeaklyCompressibleMultiSpecies>>>(
                        oriented_box_by_cell, mixture));
            }
        }
        // applied to initialization
        initialization_pipeline.insert_hook(
            InitializationHookPoint::AfterInitialCondition, [&]()
            { bi_directional_bd.tagBufferParticles(); });

        initialization_pipeline.insert_hook(
            InitializationHookPoint::InitialCondition, [&]()
            { supplementary_conditions.exec(); });

        auto &surface_particle_count = main_methods.template addReduceDynamics<
            QuantityReduce, ReduceSum<int>, SimpleEvaluation<DirectValue<int>>>(oriented_box_by_cell, "Indicator");

        initialization_pipeline.insert_hook(
            InitializationHookPoint::PreSimulationSanityCheck, [&]()
            { 
                if (surface_particle_count.exec() == 0)
                {
                    std::cout << "\n------------------------------------------------------------" << std::endl;
                    std::cout << "FluidDynamicsBuilder::buildBoundaryConditionsIfPresent:" << std::endl;
                    std::cout << "Error: no surface particles for bi_directional boundary at OrientBox '" 
                              << oriented_box_name << "' !" << std::endl;
                    std::cout << "------------------------------------------------------------" << std::endl;
                    exit(1);
                } });
        // applied to simulation
        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            {   
                Real dt = time_stepper.getGlobalTimeStepSize();
                bi_directional_bd.applyBoundaryCondition(dt);
                supplementary_conditions.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { bi_directional_bd.injectParticles(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleDeletionTagging, [&]()
            { bi_directional_bd.indicateOutFlowParticles(); });
        fluid_solver_config.particle_deletion_ = true; // enable particle deletion

        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterUpdateConfiguration, [&]()
            { bi_directional_bd.tagBufferParticles(); });

        return;
    }
    if (type == "free_stream")
    {
        // Emitter strip injects particles; buffer sponge imposes the inflow ramp and pins the
        // shift of freshly injected particles; disposer marks outflow particles for deletion.
        OrientedBox &buffer_box = config_manager.getEntity<OrientedBox>(config.at("buffer_box").get<std::string>());
        OrientedBox &disposer_box = config_manager.getEntity<OrientedBox>(config.at("disposer_box").get<std::string>());

        auto &emitter = fluid_body.addBodyPart<OrientedBoxByParticle>(oriented_box);
        auto &buffer = fluid_body.addBodyPart<OrientedBoxByCell>(buffer_box);
        auto &disposer = fluid_body.addBodyPart<OrientedBoxByCell>(disposer_box);

        Real target_speed = scaling_config.jsonToReal(config.at("target_speed"), "Speed");
        Real t_ref = scaling_config.jsonToReal(config.at("t_ref"), "Time");
        StartupToConstantInflowSpeed inflow_speed(target_speed, t_ref);

        auto &injection = main_methods.template addStateDynamics<EmitterInflowInjectionCK>(emitter);
        auto &inflow_condition = main_methods.template addStateDynamics<EmitterInflowConditionCK, StartupToConstantInflowSpeed>(buffer, inflow_speed);
        auto &free_stream_condition = main_methods.template addStateDynamics<FreeStreamCondition<StartupToConstantInflowSpeed>>(fluid_body, inflow_speed);
        auto &disposer_indication = main_methods.template addStateDynamics<WithinDisposerIndication>(disposer);
        auto &shift_pin = main_methods.template addStateDynamics<ConstantConstraintCK, Vecd>(buffer, "Displacement", Vecd::Zero());

        fluid_solver_config.particle_deletion_ = true;

        simulation_pipeline.insert_hook(
            SimulationHookPoint::BoundaryCondition, [&]()
            { free_stream_condition.exec(); inflow_condition.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleCreation, [&]()
            { injection.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::ParticleDeletionTagging, [&]()
            { disposer_indication.exec(); });

        simulation_pipeline.insert_hook(
            SimulationHookPoint::AfterLinearCorrectionMatrix, [&]()
            { shift_pin.exec(); });

        return;
    }
    throw std::runtime_error(
        "FluidDynamicsBuilder::buildBoundaryConditionsIfPresent: unsupported: " + type);
}
//=================================================================================================//
AbstractBidirectionalBoundary &FluidDynamicsBuilder::createBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    if (config.contains("pressure"))
    {
        SPHBody &sph_body = oriented_box_by_cell.getSPHBody();
        std::string body_name = sph_body.Name();
        if (config_manager.hasEntity<WeaklyCompressibleFluid>(body_name + "WeaklyCompressibleFluid"))
        {
            auto &bi_directional_bd = main_methods.template addGeneralDynamics<
                BidirectionalBoundaryCK, LinearCorrectionCK, PressurePrescribed<WeaklyCompressibleFluid>>(
                oriented_box_by_cell, scaling_config.jsonToReal(config.at("pressure"), "Pressure"));
            return bi_directional_bd;
        }

        if (sph_body.isMatterMaterial<WeaklyCompressibleMixture>())
        {
            auto &bi_directional_bd = main_methods.template addGeneralDynamics<
                BidirectionalBoundaryCK, LinearCorrectionCK, PressurePrescribed<WeaklyCompressibleMixture>>(
                oriented_box_by_cell, scaling_config.jsonToReal(config.at("pressure"), "Pressure"));
            return bi_directional_bd;
        }
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::createBiDirectionBoundary: unsupported boundary condition type");
}
//=================================================================================================//
} // namespace SPH
