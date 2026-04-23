/**
 * @file simulation.cpp
 * @brief 3D dambreak example driven by a JSON configuration file.
 * @details All simulation parameters (domain, fluid, wall, gravity, solver,
 *          end time) are loaded from data/config.json via the SPHSimulation
 *          facade. No hard-coded physics constants appear in this file.
 * @author Xiangyu Hu
 */
#include "sph_simulation.h"
#include <gtest/gtest.h>

using namespace SPH;

/*TEST(simulations, dambreak)
{
    SPHSimulation sim("input/dambreak.json");
    sim.resetOutputRoot("./dambreak");
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run();
}*/

TEST(simulations, t_pipe)
{
    SPHSimulation sim("input/t_pipe.json");
    sim.resetOutputRoot("./t_pipe", true);
    sim.loadConfig();
    sim.initializeSimulation();
    sim.run();
}

int main(int argc, char *argv[])
{
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}