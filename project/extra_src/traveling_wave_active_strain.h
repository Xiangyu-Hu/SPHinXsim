/**
 * @file    traveling_wave_active_strain.h
 * @brief   Imposes a travelling wave of active strain on the particles of the
 *          active region. The wave amplitude, frequency, wavelength and ramp
 *          come from the configuration, so any body with an active material
 *          can be driven this way.
 * @author  Pruthvik Arasikere Mallikarjuna and Xiangyu Hu
 */

#ifndef TRAVELING_WAVE_ACTIVE_STRAIN_H
#define TRAVELING_WAVE_ACTIVE_STRAIN_H

#include "base_local_dynamics.h"
#include "sph_system.h"
#include "sphinxsim_project.h"

namespace SPH
{
class ActiveStrainConfig;
class TravelingWaveActiveStrain : public LocalDynamics
{
  public:
    TravelingWaveActiveStrain(SPHBody &sph_body, const ActiveStrainConfig &config);

    struct UpdateKernel
    {
        template <typename ExecutionPolicy>
        UpdateKernel(const ExecutionPolicy &ex_policy, TravelingWaveActiveStrain &encloser)
            : physical_time_(encloser.sv_physical_time_->DelegatedData(ex_policy)),
              material_id_(encloser.dv_material_id_->DelegatedData(ex_policy)),
              pos0_(encloser.dv_pos0_->DelegatedData(ex_policy)),
              active_strain_(encloser.dv_active_strain_->DelegatedData(ex_policy)),
              center_(encloser.center_), region_span_(encloser.region_span_),
              core_thickness_(encloser.core_thickness_),
              amplitude_(encloser.amplitude_), frequency_(encloser.frequency_),
              wavelength_factor_(encloser.wavelength_factor_), start_time_(encloser.start_time_) {}

        void update(size_t index_i, Real dt = 0.0)
        {
            if (material_id_[index_i] == 0)
            {
                Real x = pos0_[index_i][0] - center_[0];
                Real y = pos0_[index_i][1];

                Real w = 2 * Pi * frequency_;
                Real lambda = wavelength_factor_ * region_span_;
                Real wave_number = 2 * Pi / lambda;
                Real hx = -(math::pow(x, Real(2)) - math::pow(region_span_, Real(2))) /
                          math::pow(region_span_, Real(2));
                Real current_time = *physical_time_;
                Real strength = 1 - math::exp(-current_time / start_time_);

                // The two sides of the mid line are driven half a period apart.
                Real phase_shift = y > (center_[1] + core_thickness_ / 2) ? Real(0) : Pi / 2;
                active_strain_[index_i](0, 0) =
                    -amplitude_ * hx * strength *
                    math::pow(math::sin(w * current_time / 2 + wave_number * x / 2 + phase_shift), Real(2));
            }
        }

      protected:
        Real *physical_time_;
        int *material_id_;
        Vecd *pos0_;
        Matd *active_strain_;
        Vecd center_;
        Real region_span_, core_thickness_;
        Real amplitude_, frequency_, wavelength_factor_, start_time_;
    };

  protected:
    SingleVariable<Real> *sv_physical_time_;
    DiscreteVariable<int> *dv_material_id_;
    DiscreteVariable<Vecd> *dv_pos0_;
    DiscreteVariable<Matd> *dv_active_strain_;
    Vecd center_;
    Real region_span_, core_thickness_;
    Real amplitude_, frequency_, wavelength_factor_, start_time_;
};
} // namespace SPH

#endif // TRAVELING_WAVE_ACTIVE_STRAIN_H