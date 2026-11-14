#ifndef JSON_SIMULATION_RUNNER_H
#define JSON_SIMULATION_RUNNER_H

#include "sph_simulation.h"

#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>

namespace simulation_test
{
namespace fs = std::filesystem;

class TemporaryConfig
{
  public:
    explicit TemporaryConfig(const fs::path &output_root)
    {
        fs::create_directories(output_root);
        std::random_device random;
        do
        {
            directory_ = output_root / ("test_config_" + std::to_string(random()));
        } while (!fs::create_directory(directory_));
        path_ = directory_ / "config.json";
    }

    ~TemporaryConfig()
    {
        std::error_code error;
        fs::remove(path_, error);
        if (error)
            ADD_FAILURE() << "Could not remove temporary config: " << error.message();
        fs::remove(directory_, error);
        if (error)
            ADD_FAILURE() << "Could not remove temporary config directory: " << error.message();
    }

    const fs::path &path() const { return path_; }

    void write(const json &config) const
    {
        std::ofstream file(path_);
        file << config.dump(2) << '\n';
        file.close();
        if (!file)
            throw std::runtime_error("Could not write temporary config: " + path_.string());
    }

  private:
    fs::path directory_;
    fs::path path_;
};

inline void runSimulation(const fs::path &config, const fs::path &input_dir,
                          const fs::path &output_root, bool reset_output)
{
    SPH::SPHSimulation sim(config);
    SPH::IO::getEnvironment().resetInputFolder(input_dir.string(), true);
    if (reset_output)
        sim.resetOutputRoot(output_root, true);
    sim.buildGeometries();
    sim.generateParticles();
    sim.buildSimulation();
    sim.initializeSimulation();
    sim.run();
}

inline fs::path checkpointPath(const fs::path &output_root, int step)
{
    std::ostringstream name;
    name << "Restart_" << std::setfill('0') << std::setw(10) << step << ".xml";
    return output_root / "restart" / name.str();
}

inline void runJsonSimulation(const fs::path &config)
{
    ASSERT_TRUE(fs::exists(config)) << "Missing config file: " << config.string();

    std::ifstream input(config);
    ASSERT_TRUE(input.is_open()) << "Could not open config file: " << config.string();
    json settings;
    input >> settings;

    const fs::path output_root = fs::path("./") / config.stem();
    const fs::path input_dir = fs::absolute(config).parent_path();
    if (!settings.contains("restart"))
    {
        runSimulation(config, input_dir, output_root, true);
        return;
    }

    auto &restart = settings.at("restart");
    const int save_interval = restart.value("save_interval", SPH::RestartConfig{}.save_interval_);
    const int restore_step = restart.value("restore_step", 0);
    ASSERT_GT(save_interval, 0) << "restart.save_interval must be positive";
    ASSERT_GE(restore_step, 0) << "restart.restore_step must be nonnegative";

    if (restore_step > 0)
    {
        ASSERT_TRUE(fs::exists(checkpointPath(output_root, restore_step)))
            << "Missing restart checkpoint: " << checkpointPath(output_root, restore_step);

        auto &io = SPH::IO::initEnvironment();
        io.resetOutputFolder((output_root / "restart_output").string(), true);
        io.resetRestartFolder((output_root / "restart").string(), true);
        io.resetReloadFolder((output_root / "reload").string(), true);
        std::cout << "Running restart simulation from step " << restore_step << ": " << config << std::endl;
        runSimulation(config, input_dir, output_root, false);
        return;
    }

    TemporaryConfig temporary_config(output_root);
    restart["restore_step"] = 0;
    temporary_config.write(settings);
    std::cout << "Running direct simulation: " << config << std::endl;
    runSimulation(temporary_config.path(), input_dir, output_root, true);

    ASSERT_TRUE(fs::exists(checkpointPath(output_root, save_interval)))
        << "Direct run did not reach restart save interval " << save_interval;

    restart["restore_step"] = save_interval;
    temporary_config.write(settings);
    SPH::IO::getEnvironment().resetOutputFolder((output_root / "restart_output").string(), true);
    std::cout << "Running restart simulation from step " << save_interval << ": " << config << std::endl;
    runSimulation(temporary_config.path(), input_dir, output_root, false);
}
} // namespace simulation_test

#endif // JSON_SIMULATION_RUNNER_H
