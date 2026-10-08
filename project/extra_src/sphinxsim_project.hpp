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
    return false;
}
//=================================================================================================//
} // namespace SPH