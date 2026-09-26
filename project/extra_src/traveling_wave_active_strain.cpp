#include "traveling_wave_active_strain.h"

#include "sphinxsim_project.h"

namespace SPH
{
//=================================================================================================//
TravelingWaveActiveStrain::TravelingWaveActiveStrain(SPHBody &sph_body, const ActiveStrainConfig &config)
    : LocalDynamics(sph_body),
      sv_physical_time_(&sph_system_->svPhysicalTime()),
      dv_material_id_(particles_->getVariableByName<int>("MaterialID")),
      dv_pos0_(particles_->registerStateVariableFrom<Vecd>("InitialPosition", "Position")),
      dv_active_strain_(particles_->getVariableByName<Matd>("ActiveStrain")),
      center_(config.wave_center_), region_span_(config.wave_span_),
      core_thickness_(config.wave_core_), amplitude_(config.amplitude_),
      frequency_(config.frequency_), wavelength_factor_(config.wavelength_factor_),
      start_time_(config.start_time_)
{
    // The reference position and the material id describe the undeformed
    // state, so both are kept across a restart.
    particles_->addEvolvingVariable<Vecd>("InitialPosition");
    particles_->addEvolvingVariable<int>("MaterialID");
}
//=================================================================================================//
} // namespace SPH