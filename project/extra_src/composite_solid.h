/**
 * @file    composite_solid.h
 * @brief   Three-region composite elastic solid: one active-strain region and
 *          two Saint-Venant regions, selected per particle by MaterialID.
 * @author  Pruthvik Arasikere Mallikarjuna and Xiangyu Hu
 */

#ifndef COMPOSITE_SOLID_H
#define COMPOSITE_SOLID_H

#include "active_model.h"
#include "complex_solid.h"
#include "elastic_solid.h"

namespace SPH
{

class CompositeSolidMaterial : public CompositeSolid
{
  public:
    CompositeSolidMaterial(
        Real rho0, Real youngs_modulus_active,
        Real youngs_modulus_1, Real youngs_modulus_2, Real poisson,
        StdVec<Shape *> region_shapes, StdVec<int> region_ids, int default_id)
        : CompositeSolid(rho0), region_shapes_(region_shapes),
          region_ids_(region_ids), default_id_(default_id)
    {
        add<ActiveModelSolid>(rho0, youngs_modulus_active, poisson);
        add<SaintVenantKirchhoffSolid>(rho0, youngs_modulus_1, poisson);
        add<SaintVenantKirchhoffSolid>(rho0, youngs_modulus_2, poisson);
    }

    virtual void initializeLocalParameters(BaseParticles *base_particles) override
    {
        CompositeSolid::initializeLocalParameters(base_particles);
        dv_material_id_ = base_particles->getVariableByName<int>("MaterialID");
        auto *dv_pos = base_particles->getVariableByName<Vecd>("Position");

        if (region_shapes_.size() != region_ids_.size())
        {
            throw std::runtime_error(
                "RegionShapeMaterialId: number of region shapes and ids must match.");
        }
        // Resolve the id of every particle now, on the host, using the shape
        // containment query. The first region that contains a particle wins.
        Vecd *pos = dv_pos->Data();
        int *material_id = dv_material_id_->Data();
        for (size_t index_i = 0; index_i != base_particles->TotalRealParticles(); ++index_i)
        {
            int assigned = default_id_;
            for (size_t r = 0; r != region_shapes_.size(); ++r)
            {
                if (region_shapes_[r]->checkContain(pos[index_i]))
                {
                    assigned = region_ids_[r];
                    break;
                }
            }
            material_id[index_i] = assigned;
        }
    }

    class ConstituteKernel
    {
      public:
        template <typename ExecutionPolicy>
        ConstituteKernel(const ExecutionPolicy &ex_policy, CompositeSolidMaterial &encloser)
            : material_id_(encloser.dv_material_id_->DelegatedData(ex_policy)),
              active_kernel_(ex_policy, static_cast<ActiveModelSolid &>(*encloser.composite_materials_[0])),
              svk1_kernel_(ex_policy, static_cast<SaintVenantKirchhoffSolid &>(*encloser.composite_materials_[1])),
              svk2_kernel_(ex_policy, static_cast<SaintVenantKirchhoffSolid &>(*encloser.composite_materials_[2])),
              rho0_(encloser.ReferenceDensity()), c0_(encloser.ReferenceSoundSpeed()) {}

        inline Matd StressPK1(const Matd &F, size_t index_i)
        {
            if (material_id_[index_i] == 0)
                return active_kernel_.StressPK1(F, index_i);
            else if (material_id_[index_i] == 1)
                return svk1_kernel_.StressPK1(F, index_i);
            else
                return svk2_kernel_.StressPK1(F, index_i);
        }

        inline Real VolumetricKirchhoff(Real J) { return 0.0; }

        inline Real PairNumericalDamping(Real dE_dt_ij, Real smoothing_length)
        {
            return 0.5 * rho0_ * c0_ * dE_dt_ij * smoothing_length;
        }

      protected:
        int *material_id_;
        ActiveModelSolid::ConstituteKernel active_kernel_;
        SaintVenantKirchhoffSolid::ConstituteKernel svk1_kernel_;
        SaintVenantKirchhoffSolid::ConstituteKernel svk2_kernel_;
        Real rho0_, c0_;
    };

  protected:
    DiscreteVariable<int> *dv_material_id_;
    StdVec<Shape *> region_shapes_;
    StdVec<int> region_ids_;
    int default_id_;
};
} // namespace SPH

#endif // COMPOSITE_SOLID_H