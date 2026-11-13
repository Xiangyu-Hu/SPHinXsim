#include "sphinxsim_project.h"

#include "base_body.hpp"
#include "flow_rate_bidirectional_boundary_ck.hpp"
#include "sph_simulation.h"
#include "sphinxsys_entity.h"

#include <fstream>
#include <iomanip>
#include <memory>

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
bool addExtraMaterial(EntityManager &config_manager, SPHBody &sph_body,
                      const json &config, const std::string &type)
{
    return false;
}
//=================================================================================================//
AbstractBidirectionalBoundary *createExtraBiDirectionBoundary(
    OrientedBoxByCell &oriented_box_by_cell, EntityManager &config_manager,
    MainMethods &main_methods, const json &config)
{
    if (config.contains("inflow_rate"))
    {
        auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
        SPHBody &sph_body = oriented_box_by_cell.getSPHBody();
        std::string body_name = sph_body.Name();
        std::string oriented_box_name = config.at("oriented_box").get<std::string>();
        Real q_target = scaling_config.jsonToReal(config.at("inflow_rate"), "VolumetricFlowRate");
        if (!config.contains("inflow_area"))
        {
            throw std::runtime_error(
                "createExtraBiDirectionBoundary: inflow_rate at OrientedBox '" + oriented_box_name +
                "' requires inflow_area.");
        }
        // cross-section area is given by the user, no assumption on the shape of the inlet
        Real length_ref = scaling_config.getScalingRef("Length");
        Real area = config.at("inflow_area").get<Real>() / (length_ref * length_ref);
        if (area <= Real(0))
        {
            throw std::runtime_error(
                "createExtraBiDirectionBoundary: inflow_area at OrientedBox '" + oriented_box_name +
                "' must be positive.");
        }
        auto &velocity_increment_manager = main_methods.addGeneralDynamics<VelocityIncrementCK>(
            oriented_box_by_cell, q_target, area);
        // handed over to addExtraBiDirectionBoundaryHooks, which owns the pipeline access
        config_manager.addEntity(oriented_box_name + "VelocityIncrement", &velocity_increment_manager);

        if (config_manager.hasEntity<WeaklyCompressibleFluid>(body_name + "WeaklyCompressibleFluid"))
        {
            return &main_methods.addGeneralDynamics<
                BidirectionalBoundaryCK, LinearCorrectionCK, FlowRatePrescribed<WeaklyCompressibleFluid>>(
                oriented_box_by_cell, velocity_increment_manager.svReferencePressure());
        }

        if (sph_body.isMatterMaterial<WeaklyCompressibleMixture>())
        {
            return &main_methods.addGeneralDynamics<
                BidirectionalBoundaryCK, LinearCorrectionCK, FlowRatePrescribed<WeaklyCompressibleMixture>>(
                oriented_box_by_cell, velocity_increment_manager.svReferencePressure());
        }
    }
    return nullptr;
}
//=================================================================================================//
void addExtraBiDirectionBoundaryHooks(
    EntityManager &config_manager,
    StagePipeline<InitializationHookPoint> &initialization_pipeline,
    StagePipeline<SimulationHookPoint> &simulation_pipeline,
    TimeStepper &time_stepper, const json &config)
{
    using FlowController = VelocityIncrementCK<MainExecutionPolicy>;
    std::string oriented_box_name = config.at("oriented_box").get<std::string>();
    if (!config_manager.hasEntity<FlowController>(oriented_box_name + "VelocityIncrement"))
    {
        return;
    }

    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    auto *flow_controller =
        &config_manager.getEntity<FlowController>(oriented_box_name + "VelocityIncrement");
    std::shared_ptr<std::ofstream> flow_rate_file;
    if (config.value("record_flow_rate", false))
    {
        const std::string path = IO::getEnvironment().OutputFolder() + "/" +
                                 oriented_box_name + "_FlowRate.dat";
        flow_rate_file = std::make_shared<std::ofstream>(path, std::ios::out);
        if (!*flow_rate_file)
        {
            throw std::runtime_error("Cannot open flow rate recording file: " + path);
        }
        *flow_rate_file << "time_s Q_target_m3_s Q_measured_m3_s\n"
                        << std::setprecision(17);
    }
    auto *time_stepper_ptr = &time_stepper;
    const Real flow_rate_scale = scaling_config.getScalingRef("VolumetricFlowRate");
    auto update_and_record = [flow_controller, flow_rate_file, flow_rate_scale, time_stepper_ptr]()
    {
        flow_controller->updateVelocityIncrement();
        if (flow_rate_file)
        {
            *flow_rate_file << time_stepper_ptr->getPhysicalTimeWithScalingRef() << ' '
                            << flow_controller->targetFlowRate() * flow_rate_scale << ' '
                            << flow_controller->measuredFlowRate() * flow_rate_scale << std::endl;
        }
    };
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialAfterLinearCorrectionMatrix, update_and_record);
    simulation_pipeline.insert_hook(
        SimulationHookPoint::AfterLinearCorrectionMatrix, update_and_record);
}
//=================================================================================================//
} // namespace SPH
