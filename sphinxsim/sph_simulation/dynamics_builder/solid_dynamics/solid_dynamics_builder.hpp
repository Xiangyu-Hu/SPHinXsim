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

#ifdef SPHINXSIM_PROJECT
#include "sphinxsim_project.hpp"
#endif
namespace SPH
{
//=================================================================================================//
using namespace solid_dynamics;
//=================================================================================================//
template <class InnerRelationType>
void SolidDynamicsBuilder::buildSolidRelaxation1stHalf(
    EntityManager &config_manager, ParticleDynamicsGroup &solid_relaxation_1st_half,
    MainMethods &main_methods, InnerRelationType &inner_relation)
{
    auto &solid_body = inner_relation.getDynamicsIdentifier();
    if (config_manager.hasEntity<SaintVenantKirchhoffSolid>(
            solid_body.Name() + "SaintVenantKirchhoffSolid"))
    {
        solid_relaxation_1st_half.add(
            &main_methods.template addInteractionDynamicsWithUpdate<
                StructureNumericalDamping, SaintVenantKirchhoffSolid>(inner_relation));
        solid_relaxation_1st_half.add(
            &main_methods.template addInteractionDynamicsOneLevel<
                StructureIntegration1stHalfPK2, SaintVenantKirchhoffSolid>(inner_relation));
        return;
    }

#ifdef SPHINXSIM_PROJECT
    if (addExtraSolidRelaxation1stHalf(
            config_manager, solid_relaxation_1st_half, main_methods, inner_relation))
    {
        return;
    }
#endif

    throw std::runtime_error(
        "SolidDynamicsBuilder::buildSolidRelaxation1stHalf: No valid solid material found for " +
        solid_body.Name());
}
//=================================================================================================//
} // namespace SPH
#endif // SOLID_DYNAMICS_BUILDER_HPP
