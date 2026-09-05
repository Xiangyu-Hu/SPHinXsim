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
 * @file    recording_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef RECORDING_BUILDER_H
#define RECORDING_BUILDER_H

#include "base_simulation_builder.h"
#include "sph_solver.h"

namespace SPH
{
class IODynamicsGroup;
class BaseIO;
class BodyStatesRecording;
class SPHBody;

template <class ExecutionPolicy>
class BodyStatesRecordingToVtpCK;
using MainBodyStatesRecording = BodyStatesRecordingToVtpCK<MainExecutionPolicy>;

struct ObserverConfig
{
    std::string name_;
    std::string observed_body_;
    VariableConfig observed_variable_;
};

class RecordingBuilder
{
  public:
    static void buildObservationIfPresent(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    // Reduced-quantity recording (e.g. total mechanical energy) driven from a
    // JSON "energy_recording" list, generic over body and quantity type.
    static void buildEnergyRecordingIfPresent(SPHSimulation &sim, MainMethods &main_methods, const json &config);
    static void createBodyStatesRecording(SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods);
    static void finalizeBodyStatesRecording(SPHSystem &sph_system, EntityManager &config_manager, const json &config);
    static MainBodyStatesRecording &getBodyStatesRecording(EntityManager &config_manager);

  private:
    static std::string getObserverRelationName(const ObserverConfig &observer_config);
    static ObserverConfig parseObserverConfig(const json &config);
    static void addObserves(SPHSystem &sph_system, EntityManager &config_manager, const json &config);

    static ParticleDynamicsGroup &createObserverConfigurationDynamics(
        SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods);

    static IODynamicsGroup &addObserveRecorder(
        SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods);

    template <class ObserverRelationType>
    static BaseIO *addObserveRecorderWithVariableConfig(
        const ScalingConfig &scaling_config, const VariableConfig &variable_config,
        MainMethods &main_methods, ObserverRelationType &observer_relation);

    static void addVariableToStateRecorder(
        BodyStatesRecording &state_recording, SPHBody &sph_body, const json &config);
};
} // namespace SPH
#endif // RECORDING_BUILDER_H
