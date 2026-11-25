/**
 * @file    regression_test_builder.h
 * @brief   The "regression_test" entry of the JSON: its quantities and the scheduling of
 *          generateDataBase()/testResult() at the end time.
 * @details Project stage only. In the second step
 *          - RegressionTestQuantity is to be inserted into
 *            sphinxsim/sph_simulation/simulation_builder/base_simulation_builder.h,
 *            between RestartConfig and GravityConfig;
 *          - buildRegressionTestIfPresent becomes a member of SimulationBuilder, declared in
 *            base_simulation_builder.h right after buildRestartFromFileIfPresent and called
 *            right after it in FluidSimulationBuilder::buildSimulation and
 *            ContinuumSimulationBuilder::buildSimulation. As a member it takes
 *            (SPHSimulation &sim, const json &config) again and reads the three references
 *            from sim; here they are parameters, since a project function is not a friend
 *            of SPHSimulation.
 */

#ifndef REGRESSION_TEST_BUILDER_H
#define REGRESSION_TEST_BUILDER_H

#include "base_simulation_builder.h"

#include <functional>

namespace SPH
{
class EntityManager;
class SPHSystem;
class TimeStepper;

/** One entry of "regression_test": "quantities", keyed by its "recording" name.
 *  The recording of this name reads config_ and sets finalize_. */
struct RegressionTestQuantity
{
    json config_;
    std::function<void()> finalize_;
};

void buildRegressionTestIfPresent(
    SPHSystem &sph_system, EntityManager &config_manager,
    TimeStepper &time_stepper, const json &config);
} // namespace SPH

#endif // REGRESSION_TEST_BUILDER_H
