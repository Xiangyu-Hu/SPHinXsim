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

namespace SPH
{
class IODynamicsGroup;
class BaseIO;
class BodyStatesRecording;
class SPHBody;

struct RestartConfig
{
    bool enabled_{false};
    int save_interval_{1000};
    int restore_step_{0};
    bool summary_enabled_{false};
};

struct VariableConfig
{
    std::string type_;
    std::string name_;
};

struct ObserverConfig
{
    std::string name_;
    std::string observed_body_;
    VariableConfig observed_variable_;
};

class RecordingBuilder
{
  public:
    template <class MethodContainerType>
    void buildObservationIfPresent(SPHSimulation &sim, MethodContainerType &main_methods, const json &config);
    RestartConfig parseRestartConfig(const json &config);

    template <class MethodContainerType>
    BodyStatesRecording &createBodyStatesRecording(
        SPHSystem &sph_system, EntityManager &config_manager,
        MethodContainerType &main_methods, const json &config);

  private:
    std::string getObserverRelationName(const ObserverConfig &observer_config);
    ObserverConfig parseObserverConfig(const json &config);
    VariableConfig parseVariableConfig(const json &config);
    void addObserves(SPHSystem &sph_system, EntityManager &config_manager, const json &config);

    template <class MethodContainerType>
    ParticleDynamicsGroup &createObserverConfigurationDynamics(
        SPHSystem &sph_system, EntityManager &config_manager, MethodContainerType &main_methods);

    template <class MethodContainerType>
    IODynamicsGroup &addObserveRecorder(
        SPHSystem &sph_system, EntityManager &config_manager, MethodContainerType &main_methods);

    template <class MethodContainerType, class ObserverRelationType>
    BaseIO *addObserveRecorderWithVariableConfig(
        const ScalingConfig &scaling_config, const VariableConfig &variable_config,
        MethodContainerType &main_methods, ObserverRelationType &observer_relation);

    void addVariableToStateRecorder(
        BodyStatesRecording &state_recording, SPHBody &sph_body, const json &config);
};
} // namespace SPH
#endif // RECORDING_BUILDER_H
