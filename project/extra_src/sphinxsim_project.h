/**
 * @file    sphinxsim_project.h
 * @brief   tbd.
 * @author  Xiangyu Hu
 */

#ifndef SPHINXSIM_PROJECT_H
#define SPHINXSIM_PROJECT_H

#include "base_simulation_builder.h"
#include "simulation_scaling.h"
namespace SPH
{
class EntityManager;

bool addExtraMaterial(
    EntityManager &config_manager, SPHBody &sph_body,
    const json &config, const std::string &type);

template <class InnerRelationType>
bool addExtraSolidRelaxation1stHalf(
    EntityManager &config_manager, ParticleDynamicsGroup &solid_relaxation_1st_half,
    MainMethods &main_methods, InnerRelationType &inner_relation);
} // namespace SPH

#endif // SPHINXSIM_PROJECT_H