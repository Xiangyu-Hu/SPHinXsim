/**
 * @file simulation.cpp
 * @brief 2D dambreak example driven by a JSON configuration file.
 * @details All simulation parameters (domain, fluid, wall, gravity, solver,
 *          end time) are loaded from data/config.json via the SPHSimulation
 *          facade.  No hard-coded physics constants appear in this file.
 * @author Xiangyu Hu
 */
#include "sph_simulation.h"
#include "sphinxsys.h"

using namespace SPH;

int main() {
  SPHSimulation sim;
  sim.loadFromFile("input/config.json");
  sim.run();

  return 0;
}
