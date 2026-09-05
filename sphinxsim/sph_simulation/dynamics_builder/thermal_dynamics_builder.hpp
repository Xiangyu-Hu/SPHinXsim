#ifndef THERMAL_DYNAMICS_BUILDER_HPP
#define THERMAL_DYNAMICS_BUILDER_HPP

#include "thermal_dynamics_builder.h"

#include "material_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class DiffusionDynamicsType, class DiffusionType, class ContactRelationType>
void ThermalDynamicsBuilder::buildThermalBoundaryCondition(
    const std::string &boundary_type, DiffusionDynamicsType &diffusion_dynamics,
    DiffusionType &diffusion, ContactRelationType &contact_relation)
{
    if (boundary_type == "Dirichlet")
    {
        diffusion_dynamics.template addPostContactInteraction<
            InteractionOnly, Dirichlet<DiffusionType>, LinearCorrectionCK>(
            contact_relation, &diffusion);
        return;
    }

    if (boundary_type == "Neumann")
    {
        diffusion_dynamics.template addPostContactInteraction<
            InteractionOnly, Neumann<DiffusionType>, LinearCorrectionCK>(
            contact_relation, &diffusion);
        return;
    }

    std::runtime_error(
        "Error: the boundary type is not supported in buildThermalBoundaryCondition.");
}
//=================================================================================================//
} // namespace SPH
#endif // THERMAL_DYNAMICS_BUILDER_HPP
