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
 * @file    solid_dynamics_builder.h
 * @brief   Assembles the elastic-solid stress relaxation loop for the
 *          simulator, driving the solid sub-stepping through the time
 *          stepper's matched-interval integrator.
 * @author  Pruthvik Arasikere Mallikarjuna and Xiangyu Hu
 */

#ifndef SOLID_DYNAMICS_BUILDER_H
#define SOLID_DYNAMICS_BUILDER_H

#include "base_simulation_builder.h"

#include <functional>

namespace SPH
{
class RealBody;

class SolidDynamicsBuilder
{
  public:
    // pre_substep_hook, if given, runs once before every solid sub-step
    // (e.g. imposing an active strain), matching the SYCL reference which
    // re-samples the active strain at each solid sub-step rather than once
    // per coupling interval.
    template <class MaterialType, class MethodContainerType, class InnerRelationType>
    static auto &buildSolidDynamics(
        SPHSimulation &sim, MethodContainerType &method_container,
        InnerRelationType &inner_relation,
        std::function<void()> pre_substep_hook = nullptr);

    static void buildMaterialIdAssignmentIfPresent(
        SPHSimulation &sim, MainMethods &main_methods, const json &config);

  private:
};
} // namespace SPH
#endif // SOLID_DYNAMICS_BUILDER_H