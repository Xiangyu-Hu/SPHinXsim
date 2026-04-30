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
 * @file    base_simulation_builder.h
 * @brief   TBD.
 * @author  Xiangyu Hu
 */

#ifndef BASE_SIMULATION_BUILDER_H
#define BASE_SIMULATION_BUILDER_H

#include "data_type.h"

#include <nlohmann/json.hpp>
using json = nlohmann::json;

namespace SPH
{
/** Convert a JSON array [x, y] or [x, y, z] to Vecd (extra elements are
 * ignored). */
bool is_number(const std::string &s);
const json *find_in_array(const json &arr, const std::string &key, const std::string &value);
Real resolve(const json &j, const std::string &path);

// Enum for hook points for fast O(1) access
enum class SimulationHookPoint
{
    BoundaryCondition,
    PositionConstraint,
    ParticleCreation,
    ParticleDeletionTagging,
    ParticleDeletion,
    Observation,
    ExtraOutput,
    ParticleSort,
    ParticleIndicationTagging,
    AfterAdvectionStepSetup,
    NumHooks
};

enum class InitializationHookPoint
{
    InitialCondition,
    InitialObservation,
    InitialParticleIndicationTagging,
    InitialAfterAdvectionStepSetup,
    NumHooks
};

// A staged pipeline structure
template <typename HookPointType>
struct StagePipeline
{
    std::vector<std::function<void()>> main_steps;
    std::vector<std::function<void()>> hooks[static_cast<size_t>(HookPointType::NumHooks)];

    void run_hooks(HookPointType p)
    {
        for (auto &f : hooks[static_cast<size_t>(p)])
            f();
    }

    void insert_hook(HookPointType p, std::function<void()> step)
    {
        hooks[static_cast<size_t>(p)].push_back(std::move(step));
    }
};

class SPHSimulation;
class SPHSystem;
class EntityManager;
class BaseParticles;
class MaterialBuilder;
class ParticleDynamicsGroup;
class IODynamicsGroup;
class BaseIO;
class BodyStatesRecording;
class SPHBody;

template <class ReturnType>
class BaseDynamics;

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
    ScalingConfig(const json &config);
    Vecd jsonToVecd(const nlohmann::json &arr, const std::string &unit_name) const;
    Real jsonToReal(const json &j, const std::string &unit_name) const;
    Real getScalingRef(const std::string &unit_name) const;
#ifdef SPHINXSYS_2D
    Transform jsonToTransform(const nlohmann::json &config);
    Rotation getRotationFromXAxis(const Vecd &direction);
#else
    Transform jsonToTransform(const nlohmann::json &config);
    Rotation getRotationFromXAxis(const Vecd &direction);
#endif

  private:
    std::vector<CharacteristicDimension> character_dims_;
    Eigen::Array<Real, 7, 1> scaling_refs_ = Eigen::Array<Real, 7, 1>::Ones();

    UnitMetrics getUnitMetrics(std::string unit_name) const;
    CharacteristicDimension parseCharacteristicDimension(const json &root_config, const json &config) const;
    void computeScaling();
    bool isSameOrderOfMagnitude(const Real a, const Real b) const;
};

#ifdef SPHINXSYS_2D
Rotation getRotationFromXAxis(const Vecd &direction);
#else
Rotation getRotationFromXAxis(const Vecd &direction);
#endif

struct RestartConfig
{
    bool enabled_{false};
    int save_interval_{1000};
    int restore_step_{0};
    bool summary_enabled_{false};
};

struct SolverCommonConfig
{
    Real end_time_{0.0};
    Real output_interval_{0.1};
    UnsignedInt screen_interval_{100};
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

class SimulationBuilder
{
  public:
    SimulationBuilder();
    virtual ~SimulationBuilder();
    virtual void buildSimulation(SPHSimulation &sim, const json &config) = 0;
    virtual void parseSolverParameters(EntityManager &config_manager, const json &config);

  protected:
    void buildFluidBodies(SPHSystem &sph_system, EntityManager &config_manager, const json &config);
    void buildContinuumBodies(SPHSystem &sph_system, EntityManager &config_manager, const json &config);
    void buildSolidBodies(SPHSystem &sph_system, EntityManager &config_manager, const json &config);

    template <class MethodContainerType>
    void buildObservationIfPresent(SPHSimulation &sim, MethodContainerType &main_methods, const json &config);

    template <class MethodContainerType>
    BodyStatesRecording &createBodyStatesRecording(
        SPHSystem &sph_system, EntityManager &config_manager,
        MethodContainerType &main_methods, const json &config);

    template <class MethodContainerType>
    void buildExternalForceIfPresent(
        SPHSimulation &sim, MethodContainerType &main_methods, SPHBody &sph_body, const json &config);

  private:
    std::unique_ptr<MaterialBuilder> material_builder_ptr_;
    SolverCommonConfig parseSolverCommonConfig(const json &config);
    RestartConfig parseRestartConfig(const json &config);
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
        const VariableConfig &variable_config, MethodContainerType &main_methods,
        ObserverRelationType &observer_relation);

    void addVariableToStateRecorder(
        BodyStatesRecording &state_recording, SPHBody &sph_body, const json &config);
};
} // namespace SPH
#endif // BASE_SIMULATION_BUILDER_H
