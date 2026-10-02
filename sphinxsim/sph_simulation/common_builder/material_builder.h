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
 * @file    material_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef MATERIAL_BUILDER_H
#define MATERIAL_BUILDER_H

#include "base_simulation_builder.h"

#ifdef SPHINXSIM_PROJECT
#include "sphinxsim_project.h"
#endif

namespace SPH
{
class EntityManager;
class SPHBody;
using NamesAndDensities = StdVec<std::pair<std::string, Real>>;

struct ThermalBoundaryConfig
{
    std::string boundary_type; // "Dirichlet",  "Neumann" or "Robin"
};

class MaterialBuilder
{
  public:
    void addMaterial(EntityManager &config_manager, SPHBody &sph_body, const json &config);
    static StdVec<Real> parseMixtureFractions(const ScalingConfig &scaling_config, const json &config);

  private:
    void addMatterMaterial(EntityManager &config_manager, SPHBody &sph_body, const json &config);
    NamesAndDensities parseNamesAndDensities(const ScalingConfig &scaling_config, const json &config);
    void addOtherMaterialProperties(EntityManager &config_manager, SPHBody &sph_body, const json &config);
    void addViscosity(EntityManager &config_manager, SPHBody &sph_body, const json &config);
    void addThermalProperties(EntityManager &config_manager, SPHBody &sph_body, const json &config);
    ThermalBoundaryConfig parseThermalBoundaryConfig(const json &config);
    Real getWeaklyCompressibleSoundSpeed(EntityManager &config_manager);
};
} // namespace SPH
#endif // MATERIAL_BUILDER_H
