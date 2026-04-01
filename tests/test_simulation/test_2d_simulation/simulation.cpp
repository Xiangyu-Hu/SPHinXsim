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
    SPH::SPHSimulation sim("input/dambreak.json");
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run(20.0);
}

TEST(simulations, filling_tank)
{
    SPH::SPHSimulation sim("input/filling_tank.json");
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run(20.0);
}

int main(int argc, char *argv[])
{
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
