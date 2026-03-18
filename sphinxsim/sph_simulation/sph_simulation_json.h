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
 * @file    sph_simulation_json.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef SPH_SIMULATION_JSON_H
#define SPH_SIMULATION_JSON_H

#include "base_data_type_package.h"

#include <nlohmann/json.hpp>
using json = nlohmann::json;

namespace SPH
{
/** Convert a JSON array [x, y] or [x, y, z] to Vecd (extra elements are
 * ignored). */
inline Vecd jsonToVecd(const nlohmann::json &arr)
{
    Vecd v = Vecd::Zero();
    const int dim = static_cast<int>(Vecd::RowsAtCompileTime);
    for (int i = 0; i < std::min(dim, static_cast<int>(arr.size())); ++i)
        v[i] = arr[i].get<Real>();
    return v;
}
} // namespace SPH
#endif // SPH_SIMULATION_JSON_H
