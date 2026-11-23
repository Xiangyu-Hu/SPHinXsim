/**
 * @file    flow_rate_boundary_state.h
 * @brief   Boundary state of the flow-rate-prescribed bidirectional boundary.
 * @details Project stage only. In the second step the struct below is to be inserted into
 *          sphinxsim/sphinxsys/src/shared/shared_ck/particle_dynamics/fluid_dynamics/boundary_condition/fluid_boundary_state.h,
 *          between VelocityPrescribed and ConstantInflowSpeed.
 *          The code is kept exactly as it will be merged.
 */

#ifndef FLOW_RATE_BOUNDARY_STATE_H
#define FLOW_RATE_BOUNDARY_STATE_H

#include "fluid_boundary_state.h"

namespace SPH
{
template <class FluidType>
struct FlowRatePrescribed
{
    typedef FluidType Fluid;
    SingleVariable<Real> *sv_reference_pressure_;
    FlowRatePrescribed(SingleVariable<Real> *sv_reference_pressure)
        : sv_reference_pressure_(sv_reference_pressure) {};
    Real getPressure(const Real &input_pressure, Real time)
    {
        return sv_reference_pressure_->getValue();
    };
    Real getAxisVelocity(const Vecd &input_position, const Real &input_axis_velocity, Real time)
    {
        return input_axis_velocity;
    };
};
} // namespace SPH
#endif // FLOW_RATE_BOUNDARY_STATE_H
