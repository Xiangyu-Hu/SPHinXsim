/**
 * @file    flow_rate_bidirectional_boundary_ck.h
 * @brief   Algorithm classes of the flow-rate-prescribed bidirectional boundary:
 *          the buffer average, the velocity increment and its controller.
 * @details Project stage only. In the second step the classes below are to be appended to
 *          sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/boundary_condition/bidirectional_boundary_ck.h,
 *          right after BidirectionalBoundaryCK and inside namespace fluid_dynamics,
 *          together with the include of "general_reduce_ck.hpp".
 *          The code is kept exactly as it will be merged.
 */

#ifndef FLOW_RATE_BIDIRECTIONAL_BOUNDARY_CK_H
#define FLOW_RATE_BIDIRECTIONAL_BOUNDARY_CK_H

#include "bidirectional_boundary_ck.h"
#include "general_reduce_ck.hpp"

namespace SPH
{
namespace fluid_dynamics
{
template <typename DataType>
class BufferQuantityAverageCK : public QuantityAverage<DataType, OrientedBoxByCell>
{
    using BaseDynamicsType = QuantityAverage<DataType, OrientedBoxByCell>;

  public:
    BufferQuantityAverageCK(OrientedBoxByCell &oriented_box_part, const std::string &variable_name);
    virtual ~BufferQuantityAverageCK() {};

    class ReduceKernel : public BaseDynamicsType::ReduceKernel
    {
      public:
        template <class ExecutionPolicy, class EncloserType>
        ReduceKernel(const ExecutionPolicy &ex_policy, EncloserType &encloser);
        Sample<DataType> reduce(size_t index_i, Real dt = 0.0);

      protected:
        Sample<DataType> zero_sample_;
        OrientedBox *oriented_box_;
        Vecd *pos_;
    };

  protected:
    SingleVariable<OrientedBox> *sv_oriented_box_;
    DiscreteVariable<Vecd> *dv_pos_;
};

class VelocityIncrementApplyCK : public BaseLocalDynamics<OrientedBoxByCell>
{
  public:
    VelocityIncrementApplyCK(OrientedBoxByCell &oriented_box_part,
                             SingleVariable<Real> *sv_velocity_increment);
    virtual ~VelocityIncrementApplyCK() {};

    class UpdateKernel
    {
      public:
        template <class ExecutionPolicy, class EncloserType>
        UpdateKernel(const ExecutionPolicy &ex_policy, EncloserType &encloser);
        void update(size_t index_i, Real dt = 0.0);

      protected:
        OrientedBox *oriented_box_;
        Vecd *pos_, *vel_;
        Real *velocity_increment_;
        int axis_;
        Transform *transform_;
    };

  protected:
    SingleVariable<OrientedBox> *sv_oriented_box_;
    DiscreteVariable<Vecd> *dv_pos_, *dv_vel_;
    SingleVariable<Real> *sv_velocity_increment_;
};

template <typename ExecutionPolicy>
class VelocityIncrementCK : public AbstractDynamics
{
    static constexpr Real IncrementFactor_ = 0.2;
    ReduceDynamicsCK<ExecutionPolicy, BufferQuantityAverageCK<Vecd>> average_global_velocity_;
    ReduceDynamicsCK<ExecutionPolicy, BufferQuantityAverageCK<Real>> average_pressure_;
    Transform *transform_;
    int axis_;
    Real area_;
    Real target_flow_rate_;
    Real measured_flow_rate_;
    Real u_target_;
    SingleVariable<Real> *sv_velocity_increment_;
    SingleVariable<Real> *sv_reference_pressure_;
    StateDynamics<ExecutionPolicy, VelocityIncrementApplyCK> apply_velocity_increment_;

  public:
    VelocityIncrementCK(OrientedBoxByCell &oriented_box_part, Real target_flow_rate, Real area);
    virtual ~VelocityIncrementCK() {};

    void updateVelocityIncrement();
    Real targetFlowRate() const { return target_flow_rate_; }
    Real measuredFlowRate() const { return measured_flow_rate_; }
    SingleVariable<Real> *svReferencePressure() { return sv_reference_pressure_; };
};
} // namespace fluid_dynamics
} // namespace SPH
#endif // FLOW_RATE_BIDIRECTIONAL_BOUNDARY_CK_H
