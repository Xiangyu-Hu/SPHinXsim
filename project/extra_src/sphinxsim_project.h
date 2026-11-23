/**
 * @file    sphinxsim_project.h
 * @brief   Project: flow-rate-prescribed bidirectional boundary
 *          ("type": "bi_directional" with "inflow_rate" and "inflow_area")
 *          and regression tests of recordings (top-level "regression_test").
 * @details Extra files of this project and where they go in the second step
 *          (each file states the exact place in its own header):
 *          - flow_rate_boundary_state.h               -> sphinxsys fluid_boundary_state.h
 *          - flow_rate_bidirectional_boundary_ck.h    -> sphinxsys bidirectional_boundary_ck.h
 *          - flow_rate_bidirectional_boundary_ck.hpp  -> sphinxsys bidirectional_boundary_ck.hpp
 *          - flow_rate_boundary_builder.h/.cpp        -> FluidDynamicsBuilder in
 *            fluid_dynamics_builder.h and fluid_boundary_builder.cpp
 *          - regression_test_builder.h/.cpp           -> SimulationBuilder in
 *            base_simulation_builder.h/.cpp
 *          - regression_test_builder.hpp              -> RecordingBuilder in
 *            recording_builder.h/.hpp
 *          Interface patches in sphinxsim/sph_simulation/, each one the include of this header
 *          and calls to the functions below:
 *          - fluid_boundary_builder.cpp: createExtraBiDirectionBoundary() and
 *            addExtraBiDirectionBoundaryHooks()
 *          - fluid_simulation_builder.cpp and continuum_simulation_builder.cpp:
 *            buildExtraRegressionTestIfPresent()
 *          - recording_builder.cpp: addExtraEnergyRecording() and addExtraObserveRecorder()
 * @author  Xiangyu Hu
 */

#ifndef SPHINXSIM_PROJECT_H
#define SPHINXSIM_PROJECT_H

#include "base_simulation_builder.h"
#include "flow_rate_bidirectional_boundary_ck.h"
#include "flow_rate_boundary_builder.h"
#include "flow_rate_boundary_state.h"
#include "regression_test_builder.h"
#include "simulation_scaling.h"
namespace SPH
{
class EntityManager;
class OrientedBoxByCell;
class TimeStepper;
class BaseIO;
class Gravity;
class ObserverBody;
struct ObserverConfig;

namespace fluid_dynamics
{
class AbstractBidirectionalBoundary;
}

bool addExtraMaterial(
    EntityManager &config_manager, SPHBody &sph_body,
    const json &config, const std::string &type);

template <class InnerRelationType>
bool addExtraSolidRelaxation1stHalf(
    EntityManager &config_manager, ParticleDynamicsGroup &solid_relaxation_1st_half,
    MainMethods &main_methods, InnerRelationType &inner_relation);

fluid_dynamics::AbstractBidirectionalBoundary *createExtraBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config);

void addExtraBiDirectionBoundaryHooks(
    EntityManager &config_manager,
    StagePipeline<InitializationHookPoint> &initialization_pipeline,
    StagePipeline<SimulationHookPoint> &simulation_pipeline,
    TimeStepper &time_stepper, const json &config);

void buildExtraRegressionTestIfPresent(
    SPHSystem &sph_system, EntityManager &config_manager,
    TimeStepper &time_stepper, const json &config);

/** Returns the recording if the project creates it, otherwise nullptr. */
BaseIO *addExtraEnergyRecording(
    SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods,
    const std::string &recording_name, Real energy_scaling_ref, RealBody &body, Gravity &gravity);

/** Returns the recording if the project creates it, otherwise nullptr. */
BaseIO *addExtraObserveRecorder(
    EntityManager &config_manager, MainMethods &main_methods,
    const ObserverConfig &observer_config,
    Contact<Relation<ObserverBody, RealBody>> &observer_relation);
} // namespace SPH

#endif // SPHINXSIM_PROJECT_H
