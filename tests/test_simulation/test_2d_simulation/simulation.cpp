/**
 * @file simulation.cpp
 * @brief 2D simulation examples driven by a JSON configuration file.
 * @details All simulation parameters (domain, fluid, wall, gravity, solver,
 *          end time) are loaded from data/config.json via the SPHSimulation
 *          facade. Cases with restart settings run once from the beginning
 *          and again from the first saved checkpoint. Restart output is
 *          written to the case's restart_output directory.
 * @author Xiangyu Hu
 */
#include "sph_simulation.h"
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace
{
fs::path g_selected_json;

bool consumeJsonArg(int *argc, char **argv)
{
    for (int i = 1; i < *argc; ++i)
    {
        const std::string arg = argv[i];
        if (arg == "--json")
        {
            if (i + 1 >= *argc)
            {
                std::cerr << "Missing value for --json" << std::endl;
                return false;
            }
            g_selected_json = argv[i + 1];
            for (int j = i; j + 2 < *argc; ++j)
            {
                argv[j] = argv[j + 2];
            }
            *argc -= 2;
            --i;
            continue;
        }

        constexpr const char *kPrefix = "--json=";
        if (arg.rfind(kPrefix, 0) == 0)
        {
            g_selected_json = arg.substr(std::char_traits<char>::length(kPrefix));
            for (int j = i; j + 1 < *argc; ++j)
            {
                argv[j] = argv[j + 1];
            }
            *argc -= 1;
            --i;
        }
    }

    return true;
}

std::vector<fs::path> collectJsonConfigs(const fs::path &input_dir)
{
    if (!g_selected_json.empty())
    {
        return {g_selected_json};
    }

    std::vector<fs::path> json_files;
    if (!fs::exists(input_dir) || !fs::is_directory(input_dir))
    {
        return json_files;
    }

    for (const auto &entry : fs::directory_iterator(input_dir))
    {
        if (entry.is_regular_file() && entry.path().extension() == ".json")
        {
            json_files.push_back(entry.path());
        }
    }

    std::sort(json_files.begin(), json_files.end());
    return json_files;
}

std::string sanitizeCaseName(const std::string &name)
{
    std::string sanitized;
    sanitized.reserve(name.size());
    for (char ch : name)
    {
        if (std::isalnum(static_cast<unsigned char>(ch)))
        {
            sanitized.push_back(ch);
        }
        else
        {
            sanitized.push_back('_');
        }
    }

    if (sanitized.empty() || std::isdigit(static_cast<unsigned char>(sanitized.front())))
    {
        sanitized.insert(sanitized.begin(), '_');
    }
    return sanitized;
}

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

void runSimulation(const fs::path &config, const fs::path &input_dir,
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
} // namespace

class Json : public ::testing::TestWithParam<fs::path>
{
};

TEST(simulations, has_json)
{
    const auto json_files = collectJsonConfigs("./input");
    ASSERT_FALSE(json_files.empty()) << "No JSON examples found in ./input";
}

TEST_P(Json, run)
{
    const fs::path config = GetParam();
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
    ASSERT_GT(save_interval, 0) << "restart.save_interval must be positive";

    TemporaryConfig temporary_config(output_root);
    restart["restore_step"] = 0;
    temporary_config.write(settings);
    std::cout << "Running direct simulation: " << config << std::endl;
    runSimulation(temporary_config.path(), input_dir, output_root, true);

    std::ostringstream checkpoint_name;
    checkpoint_name << "Restart_" << std::setfill('0') << std::setw(10) << save_interval << ".xml";
    ASSERT_TRUE(fs::exists(output_root / "restart" / checkpoint_name.str()))
        << "Direct run did not reach restart save interval " << save_interval;

    restart["restore_step"] = save_interval;
    temporary_config.write(settings);
    SPH::IO::getEnvironment().resetOutputFolder((output_root / "restart_output").string(), true);
    std::cout << "Running restart simulation from step " << save_interval << ": " << config << std::endl;
    runSimulation(temporary_config.path(), input_dir, output_root, false);
}

INSTANTIATE_TEST_SUITE_P(
    json,
    Json,
    ::testing::ValuesIn(collectJsonConfigs("./input")),
    [](const ::testing::TestParamInfo<Json::ParamType> &info)
    {
        return sanitizeCaseName(info.param.stem().string());
    });

int main(int argc, char **argv)
{
    if (!consumeJsonArg(&argc, argv))
    {
        return 2;
    }

    ::testing::InitGoogleTest(&argc, argv);
    if (!g_selected_json.empty() && ::testing::GTEST_FLAG(filter) == "*")
    {
        ::testing::GTEST_FLAG(filter) = "json/Json.run/*";
    }
    return RUN_ALL_TESTS();
}
