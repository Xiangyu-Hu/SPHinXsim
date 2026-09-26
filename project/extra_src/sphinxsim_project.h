/**
 * @file    sphinxsim_project.h
 * @brief   tbd.
 * @author  Xiangyu Hu
 */

#ifndef SPHINXSIM_PROJECT_H
#define SPHINXSIM_PROJECT_H

#include "active_model.h"
#include "base_simulation_builder.h"
#include "composite_solid.h"
#include "simulation_scaling.h"
#include "traveling_wave_active_strain.h"

namespace SPH
{
struct ActiveStrainConfig
{
    Vecd wave_center_ = Vecd::Zero();
    Real wave_span_ = 0.0;
    Real wave_core_ = 0.0;
    Real amplitude_ = 0.0;
    Real frequency_ = 0.0;
    Real wavelength_factor_ = 0.0;
    Real start_time_ = 0.0;
};

class EntityManager;

bool addExtraMaterial(
    EntityManager &config_manager, SPHBody &sph_body, const json &config, const std::string &type);

template <class InnerRelationType>
bool addExtraSolidRelaxation1stHalf(
    EntityManager &config_manager, ParticleDynamicsGroup &solid_relaxation_1st_half,
    MainMethods &main_methods, InnerRelationType &inner_relation);
} // namespace SPH

#endif // SPHINXSIM_PROJECT_H