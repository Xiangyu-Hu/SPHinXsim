#include "sphinxsim_project.h"

#include "base_body.hpp"
#include "sphinxsys_entity.h"
#include "structure_surface_motion.h"

namespace SPH
{
//=================================================================================================//
using namespace solid_dynamics;    
//=================================================================================================//
template <class InnerRelationType>
bool addExtraSolidRelaxation1stHalf(
    EntityManager &config_manager, ParticleDynamicsGroup &solid_relaxation_1st_half,
    MainMethods &main_methods, InnerRelationType &inner_relation)
{
    auto &solid_body = inner_relation.getDynamicsIdentifier();
    if (config_manager.hasEntity<CompositeSolidMaterial>(solid_body.Name() + "CompositeSolid"))
    {
        const auto &active_strain_config = config_manager.getEntity<
            ActiveStrainConfig>(solid_body.Name() + "ActiveStrainConfig");

        solid_relaxation_1st_half.add(&main_methods.addStateDynamics<TravelingWaveActiveStrain>(
            solid_body, active_strain_config));

        solid_relaxation_1st_half.add(
            &main_methods.template addInteractionDynamicsWithUpdate<
                StructureNumericalDamping, CompositeSolidMaterial>(inner_relation));
        solid_relaxation_1st_half.add(
            &main_methods.template addInteractionDynamicsOneLevel<
                StructureIntegration1stHalfPK2, CompositeSolidMaterial>(inner_relation));
        return true;
    }
    return false;
}
//=================================================================================================//
} // namespace SPH