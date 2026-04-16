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
 * @file    geometry_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef GEOMETRY_BUILDER_H
#define GEOMETRY_BUILDER_H

#include "base_simulation_builder.h"
#include "sphinxsys.h"

namespace SPH
{
class EntityManager;

struct SystemDomainConfig
{
    bool prescribed_spacing_ = true;
    BoundingBoxd system_domain_bounds_ = BoundingBoxd(Vecd::Constant(-Eps), Vecd::Constant(Eps));
    Real particle_spacing_ = Eps;
    UnsignedInt min_dimension_resolution_ = 25;
    void updateSystemDomainConfig(const BoundingBoxd &shape_bounds);
    void updateParticleSpacing();
};

class GeometryBuilder
{
  public:
    void createGeometries(EntityManager &entity_manager, const json &config);
    static BoundingBoxd parseBoundingBox(const json &config);
    static TransformGeometryBox parseBox(const json &config);
    GeometricOps parseGeometricOp(const std::string &op_str);
    SystemDomainConfig parseSystemDomainConfig(const json &config);
    void parseGlobalResolution(SystemDomainConfig &system_domain_config, const json &config);
#ifdef SPHINXSYS_2D
    MultiPolygon parseMultiPolygon(const json &config);
#endif

  private:
    Shape *addShape(EntityManager &entity_manager, const json &config);
};
} // namespace SPH
#endif // GEOMETRY_BUILDER_H
