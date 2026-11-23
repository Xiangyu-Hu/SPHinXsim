/**
 * @file    flow_rate_bidirectional_boundary_ck.hpp
 * @brief   Implementation of the classes in flow_rate_bidirectional_boundary_ck.h.
 * @details Project stage only. In the second step the definitions below are to be appended to
 *          sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/boundary_condition/bidirectional_boundary_ck.hpp,
 *          right after the BidirectionalBoundaryCK constructor and inside namespace fluid_dynamics,
 *          together with the include of "sph_system.hpp".
 *          The code is kept exactly as it will be merged.
 */

#ifndef FLOW_RATE_BIDIRECTIONAL_BOUNDARY_CK_HPP
#define FLOW_RATE_BIDIRECTIONAL_BOUNDARY_CK_HPP

#include "flow_rate_bidirectional_boundary_ck.h"

#include "sph_system.hpp" // must be complete before bidirectional_boundary_ck.hpp

#include "bidirectional_boundary_ck.hpp"

namespace SPH
{
namespace fluid_dynamics
{
//=================================================================================================//
template <typename DataType>
BufferQuantityAverageCK<DataType>::
    BufferQuantityAverageCK(OrientedBoxByCell &oriented_box_part, const std::string &variable_name)
    : BaseDynamicsType(oriented_box_part, variable_name),
      sv_oriented_box_(oriented_box_part.svOrientedBox()),
      dv_pos_(this->particles_->template getVariableByName<Vecd>("Position"))
{
    this->quantity_name_ = "Buffer" + variable_name + "Average";
}
//=================================================================================================//
template <typename DataType>
template <class ExecutionPolicy, class EncloserType>
BufferQuantityAverageCK<DataType>::ReduceKernel::
    ReduceKernel(const ExecutionPolicy &ex_policy, EncloserType &encloser)
    : BaseDynamicsType::ReduceKernel(ex_policy, encloser),
      zero_sample_(ZeroData<Sample<DataType>>::value),
      oriented_box_(encloser.sv_oriented_box_->DelegatedData(ex_policy)),
      pos_(encloser.dv_pos_->DelegatedData(ex_policy)) {}
//=================================================================================================//
template <typename DataType>
Sample<DataType> BufferQuantityAverageCK<DataType>::ReduceKernel::reduce(size_t index_i, Real dt)
{
    return oriented_box_->checkContain(pos_[index_i])
               ? BaseDynamicsType::ReduceKernel::reduce(index_i, dt)
               : zero_sample_;
}
//=================================================================================================//
inline BufferFlowRateCK::BufferFlowRateCK(OrientedBoxByCell &oriented_box_part, Real area)
    : BaseDynamicsType(oriented_box_part, "Velocity"),
      transform_(&oriented_box_part.getOrientedBox().getTransform()),
      axis_(oriented_box_part.getOrientedBox().ReferenceAxis()),
      area_(area)
{
    this->quantity_name_ = "BufferFlowRate";
}
//=================================================================================================//
inline BufferFlowRateCK::FinishDynamics::FinishDynamics(BufferFlowRateCK &encloser)
    : transform_(encloser.transform_), axis_(encloser.axis_), area_(encloser.area_) {}
//=================================================================================================//
inline Real BufferFlowRateCK::FinishDynamics::Result(const Sample<Vecd> &reduced_value)
{
    Vecd u_mean_global = reduced_value.first / reduced_value.second;
    return area_ * transform_->xformBaseVecToFrame(u_mean_global)[axis_];
}
//=================================================================================================//
inline VelocityIncrementApplyCK::VelocityIncrementApplyCK(
    OrientedBoxByCell &oriented_box_part, SingleVariable<Real> *sv_velocity_increment)
    : BaseLocalDynamics<OrientedBoxByCell>(oriented_box_part),
      sv_oriented_box_(oriented_box_part.svOrientedBox()),
      dv_pos_(particles_->getVariableByName<Vecd>("Position")),
      dv_vel_(particles_->getVariableByName<Vecd>("Velocity")),
      sv_velocity_increment_(sv_velocity_increment) {}
//=================================================================================================//
template <class ExecutionPolicy, class EncloserType>
VelocityIncrementApplyCK::UpdateKernel::
    UpdateKernel(const ExecutionPolicy &ex_policy, EncloserType &encloser)
    : oriented_box_(encloser.sv_oriented_box_->DelegatedData(ex_policy)),
      pos_(encloser.dv_pos_->DelegatedData(ex_policy)),
      vel_(encloser.dv_vel_->DelegatedData(ex_policy)),
      velocity_increment_(encloser.sv_velocity_increment_->DelegatedData(ex_policy)),
      axis_(oriented_box_->ReferenceAxis()), transform_(&oriented_box_->getTransform()) {}
//=================================================================================================//
inline void VelocityIncrementApplyCK::UpdateKernel::update(size_t index_i, Real dt)
{
    if (oriented_box_->checkContain(pos_[index_i]))
    {
        Vecd frame_velocity = transform_->xformBaseVecToFrame(vel_[index_i]);
        frame_velocity[axis_] += *velocity_increment_;
        vel_[index_i] = transform_->xformFrameVecToBase(frame_velocity);
    }
}
//=================================================================================================//
template <typename ExecutionPolicy>
VelocityIncrementCK<ExecutionPolicy>::
    VelocityIncrementCK(OrientedBoxByCell &oriented_box_part, Real target_flow_rate, Real area)
    : AbstractDynamics(),
      flow_rate_(oriented_box_part, area),
      average_pressure_(oriented_box_part, "Pressure"),
      area_(area),
      u_target_(target_flow_rate / area),
      sv_velocity_increment_(oriented_box_part.getSPHSystem().registerSystemVariable<Real>(
          "VelocityIncrement_" + oriented_box_part.Name(), 0.0)),
      sv_reference_pressure_(oriented_box_part.getSPHSystem().registerSystemVariable<Real>(
          "ReferencePressure_" + oriented_box_part.Name(), 0.0)),
      apply_velocity_increment_(oriented_box_part, sv_velocity_increment_) {}
//=================================================================================================//
template <typename ExecutionPolicy>
void VelocityIncrementCK<ExecutionPolicy>::updateVelocityIncrement()
{
    Real measured_flow_rate = flow_rate_.exec();
    sv_velocity_increment_->setValue(IncrementFactor_ * (u_target_ - measured_flow_rate / area_));
    sv_reference_pressure_->setValue(average_pressure_.exec());
    apply_velocity_increment_.exec();
}
//=================================================================================================//
} // namespace fluid_dynamics
} // namespace SPH
#endif // FLOW_RATE_BIDIRECTIONAL_BOUNDARY_CK_HPP
