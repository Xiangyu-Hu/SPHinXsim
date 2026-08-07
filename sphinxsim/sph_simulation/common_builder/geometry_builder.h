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

#include <filesystem>
namespace fs = std::filesystem;

namespace SPH
{
class EntityManager;

struct SystemDomainConfig
{
    BoundingBoxd system_bounds_ = BoundingBoxd(Vecd::Constant(Eps));
    Real particle_spacing_ = Eps;
    void updateSystemDomain(const BoundingBoxd &shape_bounds);
};

#ifdef SPHINXSYS_2D
Rotation getRotationFromXAxis(const Vecd &direction);
#else
Rotation getRotationFromXAxis(const Vecd &direction);
#endif

class GeometryBuilder
{
  public:
    GeometryBuilder(const fs::path &config_path);
    ~GeometryBuilder();
    void resetInOutputRoot(const fs::path &output_root);
    void buildGeometries();
    std::map<std::string, std::pair<std::vector<double>, std::vector<double>>> getShapeBounds();
    //----------------------------------------------------------------------
    // static functions for geometry construction used in simulation builder
    //----------------------------------------------------------------------
    static std::map<std::string, std::pair<std::vector<double>, std::vector<double>>>
    getShapeBoundsFromConfigManager(EntityManager &config_manager);

    static void createGeometries(EntityManager &config_manager, const json &config);
    static BoundingBoxd parseBoundingBox(const ScalingConfig &scaling_config, const json &config);
    static TransformGeometryBox parseBox(const ScalingConfig &scaling_config, const json &config);
    static GeometricOps parseGeometricOp(const std::string &op_str);
    static SystemDomainConfig parseSystemDomainConfig(
        const BoundingBoxd &first_shape_bound, const ScalingConfig &scaling_config, const json &config);
    static Real parseGlobalResolution(
        const BoundingBoxd &first_shape_bound, const ScalingConfig &scaling_config, const json &config);
#ifdef SPHINXSYS_2D
    static MultiPolygon parseMultiPolygon(
        const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config);
#endif

  private:
    std::filesystem::path config_path_;
    EntityManager config_manager_;
    json loadConfig();

    static void addPrimitive(const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config);
    static TransformGeometryBox fetch_or_parseBox(
        const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config);

#ifdef SPHINXSYS_3D
    static TransformGeometryCylinder fetch_or_parseCylinder(
        const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config);
#endif

    static Shape *addShape(const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config);
    static GeometricShapeBox addOrientedBox(
        const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config);
};
} // namespace SPH
#endif // GEOMETRY_BUILDER_H
