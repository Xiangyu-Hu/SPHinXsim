/**
 * @file    regression_test_builder.cpp
 * @brief   Implementation of buildRegressionTestIfPresent.
 * @details Project stage only. In the second step the function below is to be moved to
 *          sphinxsim/sph_simulation/simulation_builder/base_simulation_builder.cpp,
 *          right after SimulationBuilder::buildRestartFromFileIfPresent, as
 *          SimulationBuilder::buildRegressionTestIfPresent(SPHSimulation &sim, const json &config).
 */

#include "regression_test_builder.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
void buildRegressionTestIfPresent(
    SPHSystem &sph_system, EntityManager &config_manager,
    TimeStepper &time_stepper, const json &config)
{
    if (!config.contains("regression_test"))
        return;

    const json &regression_test = config.at("regression_test");
    // read by the constructors of the regression test classes and by the recordings
    sph_system.setGenerateRegressionData(regression_test.value("generate_database", false));

    if (regression_test.contains("quantities"))
    {
        for (const auto &quantity_config : regression_test.at("quantities"))
        {
            RegressionTestQuantity quantity;
            quantity.config_ = quantity_config;
            config_manager.emplaceEntityOrThrow<RegressionTestQuantity>(
                quantity_config.at("recording").get<std::string>(), quantity);
        }
    }

    // runs when the physical time passes the end time,
    // i.e. at the beginning of the last time step
    Real end_time = config_manager.getEntity<SolverCommonConfig>("SolverCommonConfig").end_time_;
    time_stepper.getEventScheduler().schedule(
        end_time, [&config_manager]()
        {
            for (RegressionTestQuantity *quantity : config_manager.entitiesWith<RegressionTestQuantity>())
            {
                quantity->finalize_();
            } });
}
//=================================================================================================//
} // namespace SPH
