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
 * @file    simulation_scaling.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef SIMULATION_SCALING_H
#define SIMULATION_SCALING_H

#include "data_type.h"

#include <nlohmann/json.hpp>
using json = nlohmann::json;

namespace SPH
{
struct UnitMetrics
{
    // SI base units: length, mass, time, temperature,
    // amount of substance, electric current, luminous intensity
    // learned from openFOAM's unit handling.
    std::array<int, 7> exp = {0, 0, 0, 0, 0, 0, 0};

    int &operator[](size_t i) { return exp[i]; }
    int operator[](size_t i) const { return exp[i]; }
};
UnitMetrics operator+(const UnitMetrics &a, const UnitMetrics &b);
UnitMetrics operator-(const UnitMetrics &a, const UnitMetrics &b);
bool operator==(const UnitMetrics &a, const UnitMetrics &b);

struct CharacteristicDimension
{
    Real value_;
    UnitMetrics unit_metrics_;
    std::string name_;
    std::string hint_;
};

class ScalingConfig
{
  public:
    ScalingConfig() = default;
    ScalingConfig(const json &config);
    bool isScalingEnabled() const;
    Vecd jsonToVecd(const nlohmann::json &arr, const std::string &unit_name) const;
    Vec2d jsonToVec2d(const nlohmann::json &arr, const std::string &unit_name) const;
    Real jsonToReal(const json &j, const std::string &unit_name) const;
    Real getScalingRef(const std::string &unit_name, bool is_required = true) const;
#ifdef SPHINXSYS_2D
    Transform jsonToTransform(const nlohmann::json &config) const;
#else
    Transform jsonToTransform(const nlohmann::json &config) const;
#endif

  private:
    std::vector<CharacteristicDimension> character_dims_;
    Eigen::Array<Real, 7, 1> scaling_refs_ = Eigen::Array<Real, 7, 1>::Ones();

    UnitMetrics getUnitMetrics(std::string unit_name, bool is_required = true) const;
    CharacteristicDimension parseCharacteristicDimension(const json &root_config, const json &config) const;
    void computeScaling();
    bool isSameOrderOfMagnitude(const Real a, const Real b) const;
    bool is_number(const std::string &s) const;
    bool is_array_float(const json &arr) const;
    const json *resolveNode(const json &j, const std::string &path) const;
    const json *find_in_array(const json &arr, const std::string &key, const std::string &value) const;
    Real resolve(const json &j, const std::string &path) const;
};
} // namespace SPH
#endif // SIMULATION_SCALING_H
