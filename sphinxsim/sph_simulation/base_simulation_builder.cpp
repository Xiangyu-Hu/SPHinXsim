#include "base_simulation_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
bool ScalingConfig::is_number(const std::string &s) const
{
    return !s.empty() && std::all_of(s.begin(), s.end(), ::isdigit);
}
//=================================================================================================//
Real ScalingConfig::resolve(const json &j, const std::string &path) const
{
    const json *current = &j;

    size_t i = 0;
    while (i < path.size())
    {
        // extract next token (until '.' or end)
        size_t dot = path.find('.', i);
        std::string token = path.substr(i, dot - i);

        // CASE 1: array selector like name=value
        auto lb = token.find('[');
        auto rb = token.find(']');

        if (lb != std::string::npos && rb != std::string::npos)
        {
            std::string array_name = token.substr(0, lb);
            std::string condition = token.substr(lb + 1, rb - lb - 1);

            size_t eq = condition.find('=');
            if (eq == std::string::npos)
                throw std::runtime_error("Invalid selector: " + token);

            std::string key = condition.substr(0, eq);
            std::string value = condition.substr(eq + 1);

            // go into array
            auto it = current->find(array_name);
            if (it == current->end() || !it->is_array())
                throw std::runtime_error("Not an array: " + array_name);

            const json *found = find_in_array(*it, key, value);
            if (!found)
                throw std::runtime_error("No match in array: " + token);

            current = found;
        }

        // CASE 2: numeric index
        else if (current->is_array() && is_number(token))
        {
            size_t idx = std::stoul(token);
            if (idx >= current->size())
                throw std::runtime_error("Index out of range: " + token);

            current = &((*current)[idx]);
        }

        // CASE 3: normal object field
        else
        {
            if (!current->is_object())
                throw std::runtime_error("Not an object at: " + token);

            auto it = current->find(token);
            if (it == current->end())
                throw std::runtime_error("Missing key: " + token);

            current = &(*it);
        }

        if (dot == std::string::npos)
            break;
        i = dot + 1;
    }

    if (!current->is_number())
        throw std::runtime_error("Resolved value is not numeric");

    return current->get<Real>();
}
//=================================================================================================//
const json *ScalingConfig::find_in_array(
    const json &arr, const std::string &key, const std::string &value) const
{
    for (auto &el : arr)
    {
        if (!el.is_object())
            continue;

        auto it = el.find(key);
        if (it != el.end() && it->is_string() && it->get<std::string>() == value)
        {
            return &el;
        }
    }
    return nullptr;
}
//=================================================================================================//
UnitMetrics operator+(const UnitMetrics &a, const UnitMetrics &b)
{
    UnitMetrics r;
    for (int i = 0; i < 7; ++i)
        r[i] = a[i] + b[i];
    return r;
}
//=================================================================================================//
UnitMetrics operator-(const UnitMetrics &a, const UnitMetrics &b)
{
    UnitMetrics r;
    for (int i = 0; i < 7; ++i)
        r[i] = a[i] - b[i];
    return r;
}
//=================================================================================================//
bool operator==(const UnitMetrics &a, const UnitMetrics &b)
{
    for (int i = 0; i < 7; ++i)
        if (a[i] != b[i])
            return false;
    return true;
}
//=================================================================================================//
ScalingConfig::ScalingConfig(const json &config)
{
    bool user_scaling_provided = false;
    if (config.contains("characteristic_dimensions"))
    {
        bool has_length_unit = false;
        for (const auto &cd : config.at("characteristic_dimensions"))
        {
            if (cd.at("name").get<std::string>() == "Length")
                has_length_unit = true;
            character_dims_.push_back(parseCharacteristicDimension(config, cd));
        }

        if (!has_length_unit)
        {
            throw std::runtime_error(
                "ScalingConfig::ScalingConfig: Length dimension must be provided.");
        }

        computeScaling();
        user_scaling_provided = true;
    }

    std::cout << "\n------------------------------------------------------------" << std::endl;
    if (user_scaling_provided)
    {
        std::cout << "Derived from user-provided scaling configuration." << std::endl;
        std::cout << "Length: " << scaling_refs_[0] << ", Mass: " << scaling_refs_[1]
                  << ", Time: " << scaling_refs_[2] << ", Temperature: " << scaling_refs_[3]
                  << ", ElectricCurrent: " << scaling_refs_[4]
                  << ", AmountOfSubstance: " << scaling_refs_[5]
                  << ", LuminousIntensity: " << scaling_refs_[6] << std::endl;

        for (const auto &character_dim : character_dims_)
        {
            std::cout << "Characteristic Dimension hint: " << character_dim.hint_ << std::endl;
            std::cout << "Name: " << character_dim.name_ << ",  InputValue: " << character_dim.value_
                      << ", ScaledValue: " << character_dim.value_ / getScalingRef(character_dim.name_)
                      << std::endl;
        }
    }
    else
    {
        std::cout << "No user-provided scaling configuration found." << std::endl;
        std::cout << "Using default scaling (no scaling)." << std::endl;
    }
    std::cout << "------------------------------------------------------------" << std::endl;
}
//=================================================================================================//
UnitMetrics ScalingConfig::getUnitMetrics(std::string unit_name) const
{
    if (unit_name == "Dimensionless")
        return UnitMetrics{0, 0, 0, 0, 0, 0, 0};
    if (unit_name == "Length")
        return UnitMetrics{1, 0, 0, 0, 0, 0, 0};
    if (unit_name == "Mass")
        return UnitMetrics{0, 1, 0, 0, 0, 0, 0};
    if (unit_name == "Time")
        return UnitMetrics{0, 0, 1, 0, 0, 0, 0};
    if (unit_name == "Temperature")
        return UnitMetrics{0, 0, 0, 1, 0, 0, 0};
    if (unit_name == "ElectricCurrent")
        return UnitMetrics{0, 0, 0, 0, 1, 0, 0};
    if (unit_name == "AmountOfSubstance")
        return UnitMetrics{0, 0, 0, 0, 0, 1, 0};
    if (unit_name == "LuminousIntensity")
        return UnitMetrics{0, 0, 0, 0, 0, 0, 1};
    // for continuum dynamics
    if (unit_name == "Velocity" || unit_name == "Speed")
        return UnitMetrics{1, 0, -1, 0, 0, 0, 0};
    if (unit_name == "AngularVelocity")
        return UnitMetrics{0, 0, -1, 0, 0, 0, 0};
    if (unit_name == "Acceleration" || unit_name == "Gravity")
        return UnitMetrics{1, 0, -2, 0, 0, 0, 0};
    if (unit_name == "Density")
        return UnitMetrics{-3, 1, 0, 0, 0, 0, 0};
    if (unit_name == "Stress" || unit_name == "Pressure")
        return UnitMetrics{-1, 1, -2, 0, 0, 0, 0};
    if (unit_name == "Viscosity")
        return UnitMetrics{-1, 1, -1, 0, 0, 0, 0};

    throw std::runtime_error(
        "ScalingConfig::getUnitMetrics: not supported: '" + unit_name + "' found!");
}
//=================================================================================================//
CharacteristicDimension ScalingConfig::parseCharacteristicDimension(
    const json &root_config, const json &config) const
{
    CharacteristicDimension character_dim;
    character_dim.value_ = config.at("value").get<Real>();
    character_dim.name_ = config.at("name").get<std::string>();
    character_dim.unit_metrics_ = getUnitMetrics(character_dim.name_);
    if (!config.contains("hint"))
    {
        throw std::runtime_error(
            "ScalingConfig::parseCharacteristicDimension: hint is required for using '" +
            character_dim.name_ + "' for explicit intention.");
    }
    else
    {
        character_dim.hint_ = config.at("hint").get<std::string>();
        Real hint_value = resolve(root_config, character_dim.hint_);
        if (!isSameOrderOfMagnitude(character_dim.value_, hint_value))
        {
            throw std::runtime_error(
                "ScalingConfig::parseCharacteristicDimension: value of '" + character_dim.name_ +
                "' is not the same order of magnitude as its hint '" + character_dim.hint_ + "'.");
        }
    }
    return character_dim;
}
//=================================================================================================//
void ScalingConfig::computeScaling()
{
    const int N = character_dims_.size();
    const int D = scaling_refs_.size(); // number of base dimensions

    Eigen::Matrix<Real, Eigen::Dynamic, Eigen::Dynamic> A(N, D);
    Eigen::Matrix<Real, Eigen::Dynamic, 1> y(N);

    for (int i = 0; i < N; ++i)
    {
        y(i) = std::log10(ABS(character_dims_[i].value_));

        for (int j = 0; j < D; ++j)
        {
            A(i, j) = character_dims_[i].unit_metrics_[j];
        }
    }

    // Solve least squares
    Eigen::Matrix<Real, Eigen::Dynamic, 1> scaling = A.colPivHouseholderQr().solve(y);

    for (int i = 0; i < D; ++i)
    {
        scaling_refs_[i] = std::pow(10.0, scaling(i));
    }
}
//=================================================================================================//
bool ScalingConfig::isSameOrderOfMagnitude(const Real a, const Real b) const
{
    if (a == 0 || b == 0)
        return a == b; // both must be zero to be considered the same order of magnitude

    Real log_a = std::log10(ABS(a));
    Real log_b = std::log10(ABS(b));
    return ABS(log_a - log_b) < 1.0; // within one order of magnitude
}
//=================================================================================================//
Real ScalingConfig::getScalingRef(const std::string &unit_name) const
{
    UnitMetrics unit_metrics = getUnitMetrics(unit_name);
    Real scaling_factor = 1.0;
    for (int i = 0; i < scaling_refs_.size(); ++i)
    {
        scaling_factor *= std::pow(scaling_refs_[i], unit_metrics[i]);
    }
    return scaling_factor;
}
//=================================================================================================//
Vecd ScalingConfig::jsonToVecd(const nlohmann::json &arr, const std::string &unit_name) const
{
    Vecd v = Vecd::Zero();
    const int dim = static_cast<int>(Vecd::RowsAtCompileTime);
    Real scaling_ref = getScalingRef(unit_name);
    for (int i = 0; i < std::min(dim, static_cast<int>(arr.size())); ++i)
        v[i] = arr[i].get<Real>() / scaling_ref;
    return v;
}
//=================================================================================================//
Real ScalingConfig::jsonToReal(const json &j, const std::string &unit_name) const
{
    Real value = j.get<Real>();
    Real scaling_ref = getScalingRef(unit_name);
    return value / scaling_ref;
}
//=================================================================================================//
#ifdef SPHINXSYS_2D
Transform ScalingConfig::jsonToTransform(const nlohmann::json &config) const
{
    Rotation rotation(jsonToReal(config.at("rotation_angle"), "Dimensionless"));
    Vec2d translation = jsonToVecd(config.at("translation"), "Length");
    return Transform(rotation, translation);
}
//=================================================================================================//
Rotation getRotationFromXAxis(const Vecd &direction)
{
    Real angle = std::atan2(direction[yAxis], direction[xAxis]);
    return Rotation(angle);
}
//=================================================================================================//
#else
Transform ScalingConfig::jsonToTransform(const nlohmann::json &config) const
{
    Rotation rotation(jsonToReal(config.at("rotation_angle"), "Dimensionless"),
                      jsonToVecd(config.at("rotation_axis"), "Dimensionless"));
    Vec3d translation = jsonToVecd(config.at("translation"), "Length");
    return Transform(rotation, translation);
}
//=================================================================================================//
Rotation getRotationFromXAxis(const Vecd &direction)
{
    Vec3d rotation_axis = Vec3d::UnitX().cross(direction);
    Real rotation_angle = std::acos(Vec3d::UnitX().dot(direction));
    return Rotation(rotation_angle, rotation_axis);
}
#endif
//=================================================================================================//
SimulationBuilder::SimulationBuilder()
    : material_builder_ptr_(std::make_unique<MaterialBuilder>()) {}
//=================================================================================================//
SimulationBuilder ::~SimulationBuilder() = default;
//=================================================================================================//
void SimulationBuilder::buildFluidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    for (const auto &fb : config)
    {
        const std::string name = fb.at("name").get<std::string>();
        Shape &fluid_shape = config_manager.getEntity<Shape>(name);
        auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, fluid_body, fb.at("material"));
        if (fb.contains("particle_reserve_factor"))
        {
            ParticleBuffer<ReserveSizeFactor> inlet_buffer(
                scaling_config.jsonToReal(fb.at("particle_reserve_factor"), "Dimensionless"));
            fluid_body.generateParticlesWithReserve<BaseParticles, Reload>(inlet_buffer, name);
        }
        else
        {
            fluid_body.generateParticles<BaseParticles, Reload>(name);
        }
    }
}
//=================================================================================================//
void SimulationBuilder::buildContinuumBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &cb : config)
    {
        const std::string name = cb.at("name").get<std::string>();
        Shape &shape = config_manager.getEntity<Shape>(name);
        auto &continuum_body = sph_system.addBody<RealBody>(shape, name);
        material_builder_ptr_->addMaterial(config_manager, continuum_body, cb.at("material"));
        continuum_body.generateParticles<BaseParticles, Reload>(name);
    }
}
//=================================================================================================//
void SimulationBuilder::buildSolidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &sb : config)
    {
        const std::string name = sb.at("name").get<std::string>();
        Shape &solid_shape = config_manager.getEntity<Shape>(name);
        auto &solid_body = sph_system.addBody<SolidBody>(solid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, solid_body, sb.at("material"));
        BaseParticles &reload_particles = solid_body.generateParticles<BaseParticles, Reload>(name);
        reload_particles.reloadExtraVariable<Vecd>("NormalDirection");
    }
}
//=================================================================================================//
void SimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    config_manager.emplaceEntity<
        SolverCommonConfig>("SolverCommonConfig", parseSolverCommonConfig(scaling_config, config));

    if (config.contains("restart"))
    {
        config_manager.emplaceEntity<RestartConfig>(
            "RestartConfig", parseRestartConfig(config.at("restart")));
    }
}
//=================================================================================================//
SolverCommonConfig SimulationBuilder::parseSolverCommonConfig(
    const ScalingConfig &scaling_config, const json &config)
{
    SolverCommonConfig solver_common_config;
    if (config.contains("end_time"))
        solver_common_config.end_time_ = scaling_config.jsonToReal(config.at("end_time"), "Time");
    if (config.contains("output_interval"))
        solver_common_config.output_interval_ = scaling_config.jsonToReal(config.at("output_interval"), "Time");
    else
        solver_common_config.output_interval_ = solver_common_config.end_time_ / 100.0; // default to 100 output frames

    return solver_common_config;
}
//=================================================================================================//
RestartConfig SimulationBuilder::parseRestartConfig(const json &config)
{
    RestartConfig restart_config;
    restart_config.enabled_ = config.at("enabled").get<bool>();
    if (config.contains("save_interval"))
        restart_config.save_interval_ = config.at("save_interval").get<int>();
    restart_config.restore_step_ = config.at("restore_step").get<int>();
    if (config.contains("summary_enabled"))
        restart_config.summary_enabled_ = config.at("summary_enabled").get<bool>();
    return restart_config;
}
//=================================================================================================//
std::string SimulationBuilder::getObserverRelationName(const ObserverConfig &observer_config)
{
    return observer_config.name_ + observer_config.observed_body_;
}
//=================================================================================================//
ObserverConfig SimulationBuilder::parseObserverConfig(const json &config)
{
    ObserverConfig observer_config;
    observer_config.name_ = config.at("name").get<std::string>();
    observer_config.observed_body_ = config.at("observed_body").get<std::string>();
    observer_config.observed_variable_ = parseVariableConfig(config.at("variable"));
    return observer_config;
}
//=================================================================================================//
void SimulationBuilder::addObserves(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    for (const auto &ob : config)
    {
        ObserverConfig observer_config = parseObserverConfig(ob);
        std::string name = observer_config.name_;
        config_manager.emplaceEntity<ObserverConfig>(name, observer_config);

        StdVec<Vecd> positions;
        if (ob.contains("positions"))
        {
            for (const auto &p : ob.at("positions"))
            {
                positions.push_back(scaling_config.jsonToVecd(p, "Length"));
            }
        }

        ObserverBody &observer_body = sph_system.addBody<ObserverBody>(name);
        observer_body.generateParticles<ObserverParticles>(positions);
    }
}
//=================================================================================================//
VariableConfig SimulationBuilder::parseVariableConfig(const json &config)
{
    VariableConfig variable_config;
    if (config.contains("real_type"))
    {
        variable_config.type_ = "Real";
        variable_config.name_ = config.at("real_type").get<std::string>();
        return variable_config;
    }

    if (config.contains("vector_type"))
    {
        variable_config.type_ = "Vecd";
        variable_config.name_ = config.at("vector_type").get<std::string>();
        return variable_config;
    }

    throw std::runtime_error("SimulationBuilder::parseVariableConfig not supported variable type.");
}
//=================================================================================================//
void SimulationBuilder::addVariableToStateRecorder(
    BodyStatesRecording &state_recording, SPHBody &sph_body, const json &config)
{
    if (config.contains("real_type"))
    {
        StdVec<std::string> real_variables = config.at("real_type").get<StdVec<std::string>>();
        for (const auto &real_var : real_variables)
        {
            state_recording.template addToWrite<Real>(sph_body, real_var);
        }
        return;
    }

    if (config.contains("vector_type"))
    {
        StdVec<std::string> vector_variables = config.at("vector_type").get<StdVec<std::string>>();
        for (const auto &vector_var : vector_variables)
        {
            state_recording.template addToWrite<Vecd>(sph_body, vector_var);
        }
        return;
    }

    throw std::runtime_error("SimulationBuilder::addVariableToStateRecorder not supported variable type.");
}
//=================================================================================================//
} // namespace SPH
