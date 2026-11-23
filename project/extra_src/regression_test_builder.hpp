/**
 * @file    regression_test_builder.hpp
 * @brief   Wraps a recording in a regression test method when the "regression_test" entry
 *          of the JSON lists its name.
 * @details Project stage only. In the second step the code below is to be merged into
 *          sphinxsim/sph_simulation/common_builder/recording_builder.h/.hpp:
 *          - jsonToRegressionThreshold, RegressionTestOf and meanVarianceRegressionTestFinalize
 *            go to the end of recording_builder.hpp as they are;
 *          - addIODynamicsWithRegressionTest becomes a public static member of RecordingBuilder,
 *            declared right after getBodyStatesRecording and defined at the end of
 *            recording_builder.hpp. The error messages already carry that name.
 *            The energy recording, the observer recording and the flow rate recording then
 *            call it in place of MainMethods::addIODynamics;
 *          - RegressionScaledReducedQuantityRecording is ScaledReducedQuantityRecording of
 *            recording_builder.hpp with the two lines after "sv_physical_time_" in writeToFile
 *            changed. These two lines and the last two lines of the comment are to be applied
 *            to ScaledReducedQuantityRecording, and this class is to be deleted;
 *          - addObserveRecorderWithVariableConfig replaces the private member of the same name
 *            in RecordingBuilder, together with its call in RecordingBuilder::addObserveRecorder:
 *            addObserveRecorderWithVariableConfig(config_manager, *observer_config,
 *            main_methods, observer_relation).
 */

#ifndef REGRESSION_TEST_BUILDER_HPP
#define REGRESSION_TEST_BUILDER_HPP

#include "regression_test_builder.h"

#include "recording_builder.hpp"

namespace SPH
{
//=================================================================================================//
// the thresholds bound relative differences, see RegressionTestTimeAverage::compareParameter
inline void jsonToRegressionThreshold(const ScalingConfig &scaling_config, const json &config, Real &threshold)
{
    threshold = scaling_config.jsonToReal(config, "Dimensionless");
}
//=================================================================================================//
inline void jsonToRegressionThreshold(const ScalingConfig &scaling_config, const json &config, Vecd &threshold)
{
    if (config.is_number()) // the same threshold for all components
        threshold = Vecd::Constant(scaling_config.jsonToReal(config, "Dimensionless"));
    else
        threshold = scaling_config.jsonToVecd(config, "Dimensionless");
}
//=================================================================================================//
// Wraps IOType in RegressionType, so that MainMethods::addIODynamics creates and owns
// the wrapped recording in the same way as the plain one.
template <template <typename...> class RegressionType, template <typename...> class IOType>
struct RegressionTestOf
{
    template <typename... Parameters>
    using type = RegressionType<IOType<Parameters...>>;
};
//=================================================================================================//
template <class RegressionType>
std::function<void()> meanVarianceRegressionTestFinalize(
    RegressionType &regression, const ScalingConfig &scaling_config, const json &config,
    bool generate_database, const std::string &filter)
{
    using VariableType = decltype(RegressionType::type_indicator_);
    VariableType threshold_mean = ZeroData<VariableType>::value;
    VariableType threshold_variance = ZeroData<VariableType>::value;
    if (generate_database) // the thresholds only decide when the database is converged
    {
        jsonToRegressionThreshold(scaling_config, config.at("threshold_mean"), threshold_mean);
        jsonToRegressionThreshold(scaling_config, config.at("threshold_variance"), threshold_variance);
    }
    return [&regression, generate_database, threshold_mean, threshold_variance, filter]()
    {
        if (generate_database)
            regression.generateDataBase(threshold_mean, threshold_variance, filter);
        else
            regression.testResult(filter);
    };
}
//=================================================================================================//
/** Creates the recording by main_methods as addIODynamics does. If the "regression_test" entry
 *  lists recording_name, the recording is wrapped in the listed regression test method. */
template <template <typename...> class IOType, typename... Parameters, typename... Args>
IOType<MainExecutionPolicy, Parameters...> &addIODynamicsWithRegressionTest(
    SPHSystem &sph_system, EntityManager &config_manager, MainMethods &main_methods,
    const std::string &recording_name, Args &&...args)
{
    if (!config_manager.hasEntity<RegressionTestQuantity>(recording_name))
    {
        return main_methods.template addIODynamics<IOType, Parameters...>(std::forward<Args>(args)...);
    }

    auto &quantity = config_manager.getEntity<RegressionTestQuantity>(recording_name);
    if (quantity.finalize_)
    {
        throw std::runtime_error(
            "RecordingBuilder::addIODynamicsWithRegressionTest: more than one recording is named '" +
            recording_name + "'.");
    }
    bool generate_database = sph_system.GenerateRegressionData();
    const auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    std::string method = quantity.config_.at("method").get<std::string>();
    std::string filter = quantity.config_.value("filter", false) ? "true" : "false";

    if (method == "time_average")
    {
        auto &regression = main_methods.template addIODynamics<
            RegressionTestOf<RegressionTestTimeAverage, IOType>::template type, Parameters...>(
            std::forward<Args>(args)...);
        quantity.finalize_ = meanVarianceRegressionTestFinalize(
            regression, scaling_config, quantity.config_, generate_database, filter);
        return regression;
    }

    if (method == "ensemble_average")
    {
        auto &regression = main_methods.template addIODynamics<
            RegressionTestOf<RegressionTestEnsembleAverage, IOType>::template type, Parameters...>(
            std::forward<Args>(args)...);
        quantity.finalize_ = meanVarianceRegressionTestFinalize(
            regression, scaling_config, quantity.config_, generate_database, filter);
        return regression;
    }

    if (method == "dynamic_time_warping")
    {
        auto &regression = main_methods.template addIODynamics<
            RegressionTestOf<RegressionTestDynamicTimeWarping, IOType>::template type, Parameters...>(
            std::forward<Args>(args)...);
        Real threshold = generate_database
                             ? scaling_config.jsonToReal(quantity.config_.at("threshold"), "Dimensionless")
                             : Real(0);
        quantity.finalize_ = [&regression, generate_database, threshold, filter]()
        {
            if (generate_database)
                regression.generateDataBase(threshold, filter);
            else
                regression.testResult(filter);
        };
        return regression;
    }

    throw std::runtime_error(
        "RecordingBuilder::addIODynamicsWithRegressionTest: unsupported method '" + method +
        "' for recording '" + recording_name + "'.");
}
//=================================================================================================//
// SPHinXsim runs all internal dynamics in ScalingConfig's nondimensional
// units (e.g. Mass is scaled by the "Density"/"Mass" characteristic
// dimension, see base_simulation_builder.cpp jsonToReal(..., "Density")).
// The shared ReducedQuantityRecording writes whatever unit the reduce method
// produces, which is correct for plain (unscaled) SPHinXsys use, such as the
// SYCL reference, but wrong here unless converted back to physical units at
// write time - the same conversion createBodyStatesRecording and the
// observer recorder already apply via DiscreteVariable::setScalingRef.
// only the written value is converted: the observed quantity keeps
// the internal value. Becuase a regression test just needs a comparation.
template <class ExecutionPolicy, class LocalReduceMethodType>
class RegressionScaledReducedQuantityRecording : public ReducedQuantityRecording<ExecutionPolicy, LocalReduceMethodType>
{
    using Base = ReducedQuantityRecording<ExecutionPolicy, LocalReduceMethodType>;
    Real physical_scaling_ref_;

  public:
    template <class DynamicsIdentifier, typename... Args>
    RegressionScaledReducedQuantityRecording(Real physical_scaling_ref, DynamicsIdentifier &identifier, Args &&...args)
        : Base(identifier, std::forward<Args>(args)...),
          physical_scaling_ref_(physical_scaling_ref) {}

    virtual void writeToFile(size_t iteration_step = 0) override
    {
        if (!this->header_written_)
        {
            std::ofstream out_file(this->filefullpath_output_.c_str(), std::ios::out);
            out_file << "\"run_time\""
                     << "   ";
            this->plt_engine_.writeAQuantityHeader(out_file, this->reduced_quantity_, this->quantity_name_);
            out_file << "\n";
            out_file.close();
            this->header_written_ = true;
        }
        std::ofstream out_file(this->filefullpath_output_.c_str(), std::ios::app);
        out_file << this->sv_physical_time_->getValue() << "   ";
        this->reduced_quantity_ = this->reduce_method_.exec();
        this->plt_engine_.writeAQuantity(out_file, this->reduced_quantity_ * physical_scaling_ref_);
        out_file << "\n";
        out_file.close();
    }
};
//=================================================================================================//
template <typename... RelationParameters>
BaseIO *addObserveRecorderWithVariableConfig(
    EntityManager &config_manager, const ObserverConfig &observer_config,
    MainMethods &main_methods, Contact<RelationParameters...> &observer_relation)
{
    const auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    const VariableConfig &variable_config = observer_config.observed_variable_;
    SPHSystem &sph_system = observer_relation.getSPHBody().getSPHSystem();
    if (variable_config.type_ == "Real")
    {
        auto &ob = addIODynamicsWithRegressionTest<ObservedQuantityRecording, Real, RelationParameters...>(
            sph_system, config_manager, main_methods, observer_config.name_, observer_relation, variable_config.name_);
        ob.getObservedVariable().setScalingRef(scaling_config.getScalingRef(variable_config.name_));
        return &ob;
    }

    if (variable_config.type_ == "Vecd")
    {
        auto &ob = addIODynamicsWithRegressionTest<ObservedQuantityRecording, Vecd, RelationParameters...>(
            sph_system, config_manager, main_methods, observer_config.name_, observer_relation, variable_config.name_);
        ob.getObservedVariable().setScalingRef(scaling_config.getScalingRef(variable_config.name_));
        return &ob;
    }

    throw std::runtime_error(
        "RecordingBuilder::addObserveRecorderWithVariableConfig: no supported variable type found!");
}
//=================================================================================================//
} // namespace SPH
#endif // REGRESSION_TEST_BUILDER_HPP
