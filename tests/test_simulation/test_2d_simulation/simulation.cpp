/**
 * @file simulation.cpp
 * @brief 2D simulation examples driven by a JSON configuration file.
 * @details All simulation parameters (domain, fluid, wall, gravity, solver,
 *          end time) are loaded from data/config.json via the SPHSimulation
 *          facade.  No hard-coded physics constants appear in this file.
 * @author Xiangyu Hu
 */
#include "sph_simulation.h"
#include <gtest/gtest.h>

TEST(simulations, dambreak)
{
    SPH::SPHSimulation sim("./input/dambreak.json");
    sim.resetOutputRoot("./dambreak");
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run();
}

TEST(simulations, filling_tank)
{
    SPH::SPHSimulation sim("./input/filling_tank.json");
    sim.resetOutputRoot("./filling_tank", true);
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run();
}

TEST(simulations, milling)
{
    SPH::SPHSimulation sim("./input/milling.json");
    sim.resetOutputRoot("./milling", true);
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run();
}

int main(int argc, char *argv[])
{
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
