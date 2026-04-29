#include "base_simulation_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
Vecd jsonToVecd(const nlohmann::json &arr)
{
    Vecd v = Vecd::Zero();
    const int dim = static_cast<int>(Vecd::RowsAtCompileTime);
    for (int i = 0; i < std::min(dim, static_cast<int>(arr.size())); ++i)
        v[i] = arr[i].get<Real>();
    return v;
}
//=================================================================================================//
#ifdef SPHINXSYS_2D
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>());
    Vec2d translation = jsonToVecd(config.at("translation"));
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
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>(),
                      jsonToVecd(config.at("rotation_axis")));
    Vec3d translation = jsonToVecd(config.at("translation"));
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
    if (config.contains("dimensional_units"))
    {
        bool has_length_unit = false;
        for (const auto &du : config.at("dimensional_units"))
        {
            if (du.at("unit").get<std::string>() == "Length")
                has_length_unit = true;
            dimensional_units_.push_back(parseDimensionalUnit(du));
        }

        if (!has_length_unit)
        {
            throw std::runtime_error(
                "ScalingConfig::ScalingConfig: Length dimension must be provided.");
        }

        computeScaling();
    }
}
//=================================================================================================//
UnitMetrics ScalingConfig::getUnitMetrics(std::string unit_name) const
{
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
    if (unit_name == "Acceleration" || unit_name == "Gravity")
        return UnitMetrics{1, 0, -2, 0, 0, 0, 0};
    if (unit_name == "Density")
        return UnitMetrics{-3, 1, 0, 0, 0, 0, 0};
    if (unit_name == "Stress" || unit_name == "Pressure")
        return UnitMetrics{-1, 1, -2, 0, 0, 0, 0};

    throw std::runtime_error("ScalingConfig::getUnitMetrics: no supported unit name found!");
}
//=================================================================================================//
DimensionalUnit ScalingConfig::parseDimensionalUnit(const json &config) const
{
    DimensionalUnit dimensional_unit;
    dimensional_unit.value = config.at("value").get<Real>();
    dimensional_unit.unit_metrics = getUnitMetrics(config.at("unit").get<std::string>());
    return dimensional_unit;
}
//=================================================================================================//
void ScalingConfig::computeScaling()
{
    const int N = dimensional_units_.size();
    const int D = scaling_refs_.size(); // number of base dimensions

    Eigen::Matrix<Real, Eigen::Dynamic, Eigen::Dynamic> A(N, D);
    Eigen::Matrix<Real, Eigen::Dynamic, 1> y(N);

    for (int i = 0; i < N; ++i)
    {
        y(i) = std::log10(ABS(dimensional_units_[i].value));

        for (int j = 0; j < D; ++j)
        {
            A(i, j) = dimensional_units_[i].unit_metrics[j];
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
SimulationBuilder::SimulationBuilder()
    : material_builder_ptr_(std::make_unique<MaterialBuilder>()){}
//=================================================================================================//
SimulationBuilder ::~SimulationBuilder() = default;
//=================================================================================================//
void SimulationBuilder::buildFluidBodies(
    SPHSystem &sph_system, EntityManager &config_manager, const json &config)
{
    for (const auto &fb : config)
    {
        const std::string name = fb.at("name").get<std::string>();
        Shape &fluid_shape = config_manager.getEntityByName<Shape>(name);
        auto &fluid_body = sph_system.addBody<FluidBody>(fluid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, fluid_body, fb.at("material"));
        if (fb.contains("particle_reserve_factor"))
        {
            ParticleBuffer<ReserveSizeFactor> inlet_buffer(
                fb.at("particle_reserve_factor").get<Real>());
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
        Shape &shape = config_manager.getEntityByName<Shape>(name);
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
        Shape &solid_shape = config_manager.getEntityByName<Shape>(name);
        auto &solid_body = sph_system.addBody<SolidBody>(solid_shape, name);
        material_builder_ptr_->addMaterial(config_manager, solid_body, sb.at("material"));
        BaseParticles &reload_particles = solid_body.generateParticles<BaseParticles, Reload>(name);
        reload_particles.reloadExtraVariable<Vecd>("NormalDirection");
    }
}
//=================================================================================================//
void SimulationBuilder::parseSolverParameters(EntityManager &config_manager, const json &config)
{
    config_manager.emplaceEntity<
        SolverCommonConfig>("SolverCommonConfig", parseSolverCommonConfig(config));

    if (config.contains("restart"))
    {
        config_manager.emplaceEntity<RestartConfig>(
            "RestartConfig", parseRestartConfig(config.at("restart")));
    }
}
//=================================================================================================//
SolverCommonConfig SimulationBuilder::parseSolverCommonConfig(const json &config)
{
    SolverCommonConfig solver_common_config;
    if (config.contains("end_time"))
        solver_common_config.end_time_ = config.at("end_time").get<Real>();
    if (config.contains("output_interval"))
        solver_common_config.output_interval_ = config.at("output_interval").get<Real>();
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
                positions.push_back(jsonToVecd(p));
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
