#include "particle_relaxation_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void ParticleRelaxationBuilder::buildSimulation(SPHSimulation &sim, const json &config)
{
    //----------------------------------------------------------------------
    //	Build up an SPHSystem and IO environment.
    //----------------------------------------------------------------------
    RelaxationSystem &sph_system = sim.defineRelaxationSystem(config);
    EntityManager &entity_manager = sim.getEntityManager();
    //----------------------------------------------------------------------
    //	Creating bodies with inital shape, materials and particles.
    //----------------------------------------------------------------------
}
//=================================================================================================//
} // namespace SPH
