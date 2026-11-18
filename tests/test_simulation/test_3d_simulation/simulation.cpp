/**
 * @file simulation.cpp
 * @brief 3D simulation examples driven by a JSON configuration file.
 * @details All simulation parameters (domain, fluid, wall, gravity, solver,
 *          end time) are loaded from data/config.json via the SPHSimulation
 *          facade. Cases with restart settings and restore_step zero run
 *          from the beginning and again from the first saved checkpoint.
 *          A nonzero restore_step runs only from that checkpoint. Restart
 *          output is written to the case's restart_output directory.
 * @author Xiangyu Hu
 */
#include "../json_simulation_runner.h"
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <gtest/gtest.h>
#include <iostream>
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
    simulation_test::runJsonSimulation(GetParam());
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
