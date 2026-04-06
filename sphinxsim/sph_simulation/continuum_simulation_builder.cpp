#include "continuum_simulation_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void ContinuumSimulationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    SPHSystem &sph_system = sim.defineSPHSystem(config);
    EntityManager &entity_manager = sim.getEntityManager();
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
    for (const auto &cb : config.at("continuum_bodies"))
        sim.addContinuumBody(sph_system, entity_manager, cb);
    for (const auto &sb : config.at("solid_bodies"))
        sim.addSolidBody(sph_system, entity_manager, sb);
    //----------------------------------------------------------------------
    //	Define body relation map.
    //	The relations give the topological connections within a body
    //  or with other bodies within interaction range.
    //  Generally, we first define all the inner relations, then the contact relations.
    //----------------------------------------------------------------------
    auto &continuum_body = *entity_manager.entitiesWith<RealBody>().front(); // assume only one continuum body for now
    StdVec<SolidBody *> solid_bodies = entity_manager.entitiesWith<SolidBody>();

    auto &continuum_inner = sph_system.addInnerRelation(continuum_body);
    auto &continuum_solid_contact = sph_system.addContactRelation(continuum_body, solid_bodies);
    //----------------------------------------------------------------------
    // Define SPH solver with particle methods and execution policies.
    // Generally, the host methods should be able to run immediately.
    //----------------------------------------------------------------------
    SPHSolver &sph_solver = sim.defineSPHSolver(sph_system, config);
    auto &main_methods = sph_solver.addParticleMethodContainer(par_ck);
    auto &host_methods = sph_solver.addParticleMethodContainer(par_host);
    //----------------------------------------------------------------------
    // Solver control parameters.
    //----------------------------------------------------------------------
    if (config.contains("continuum_solver_parameters"))
    {
        updateSolverParameters(sim, config.at("continuum_solver_parameters"));
    }
    //----------------------------------------------------------------------
    // Define the numerical methods used in the simulation.
    // Note that there may be data dependence on the sequence of constructions.
    // Generally, the configuration dynamics, such as update cell linked list,
    // update body relations, are defined first.
    // Then the geometric models or simple objects without data dependencies,
    // such as gravity, initialized normal direction.
    // After that, the major physical particle dynamics model should be introduced.
    // Finally, the auxiliary models such as time step estimator, initial condition,
    // boundary condition and other constraints should be defined.
    //----------------------------------------------------------------------
    auto &solid_normal_direction = host_methods.addStateDynamics<NormalFromBodyShapeCK>(solid_bodies);

    auto &solid_cell_linked_list = main_methods.addCellLinkedListDynamics(solid_bodies);
    auto &fluid_update_configuration = main_methods.addParticleDynamicsGroup()
                                           .add(&main_methods.addCellLinkedListDynamics(continuum_body))
                                           .add(&main_methods.addRelationDynamics(continuum_inner, continuum_solid_contact));

    //----------------------------------------------------------------------
    //	Define the methods for I/O operations, observations
    //	and regression tests of the simulation.
    //----------------------------------------------------------------------
    auto &body_state_recorder = main_methods.addBodyStateRecorder<BodyStatesRecordingToVtpCK>(sph_system);
    for (auto &solid_body : solid_bodies)
    {
        body_state_recorder.addToWrite<Vecd>(*solid_body, "NormalDirection");
    }
    //body_state_recorder.addToWrite<Real>(continuum_body, "Pressure");
    //----------------------------------------------------------------------
    //	Define Preparation or initialization step for the time integration loop.
    //----------------------------------------------------------------------
    StagePipeline<InitializationHookPoint> &initialization_pipeline = sim.getInitializationPipeline();
    initialization_pipeline.main_steps.push_back(
        [&]()
        {
            solid_normal_direction.exec();
            initialization_pipeline.run_hooks(InitializationHookPoint::InitialConditions);

            solid_cell_linked_list.exec();
            fluid_update_configuration.exec();

            body_state_recorder.writeToFile();
        });
}
//=================================================================================================//
void ContinuumSimulationBuilder::updateSolverParameters(SPHSimulation &sim, const json &config)
{
    if (config.contains("acoustic_cfl"))
        solver_parameters_.acoustic_cfl = config.at("acoustic_cfl").get<Real>();
    if (config.contains("advection_cfl"))
        solver_parameters_.advection_cfl = config.at("advection_cfl").get<Real>();
}
//=================================================================================================//
} // namespace SPH
