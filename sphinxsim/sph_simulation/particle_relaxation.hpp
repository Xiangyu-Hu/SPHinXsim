#ifndef PARTICLE_RELAXATION_BUILDER_HPP
#define PARTICLE_RELAXATION_BUILDER_HPP

#include "particle_relaxation.h"

#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
template <class MethodContainerType>
void ParticleRelaxation::randomizeParticlePositions(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
{
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        main_methods.template addStateDynamics<RandomizeParticlePositionCK>(real_body).exec();
    }
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addDummyBodiesCellLinkedListDynamics(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &dummy_cell_linked_list = main_methods.addParticleDynamicsGroup();
    for (const auto &name : bodies_config_.dummy_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(name);
        dummy_cell_linked_list.add(&main_methods.addCellLinkedListDynamics(real_body));
    }
    return dummy_cell_linked_list;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addConfigurationDynamics(
    RelaxationSystem &relaxation_system, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &configuration_update = main_methods.addParticleDynamicsGroup();

    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        configuration_update.add(&main_methods.addCellLinkedListDynamics(real_body));
    }

    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        auto &inner_relation = relaxation_system.getRelationByName<
            Inner<Relation<RealBody>>>(body_config.name_);

        if (body_config.contact_bodies_.empty())
        {
            configuration_update.add(&main_methods.addRelationDynamics(inner_relation));
        }
        else
        {
            auto &contact_relation = relaxation_system.getRelationByName<
                Contact<Relation<RealBody, RealBody>>>(body_config.name_);
            configuration_update.add(&main_methods.addRelationDynamics(inner_relation, contact_relation));
        }
    }
    return configuration_update;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addRelaxationResidue(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &relaxation_residue = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        auto &inner_relation = relaxation_system.getRelationByName<
            Inner<Relation<RealBody>>>(body_config.name_);
        auto &residual_dynamics = main_methods.template addInteractionDynamics<
            KernelGradientIntegral, NoKernelCorrectionCK>(inner_relation);

        if (body_config.with_level_set_)
        {
            RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
            LevelSetShape &level_set_shape = entity_manager.getEntityByName<LevelSetShape>(body_config.name_);
            residual_dynamics.template addPostStateDynamics<LevelsetKernelGradientIntegral>(real_body, level_set_shape);
        }

        if (!body_config.contact_bodies_.empty())
        {
            auto &contact_relation = relaxation_system.getRelationByName<
                Contact<Relation<RealBody, RealBody>>>(body_config.name_);
            residual_dynamics.template addPostContactInteraction<Boundary, NoKernelCorrectionCK>(contact_relation);
        }
        relaxation_residue.add(&residual_dynamics);
    }
    return relaxation_residue;
}
//=================================================================================================//
template <class MethodContainerType>
BaseDynamics<Real> &ParticleRelaxation::addRelaxationScaling(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{

    auto &relaxation_scaling = main_methods.template addReduceDynamicsGroup<ReduceMin>();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        relaxation_scaling.add(&main_methods.template addReduceDynamics<RelaxationScalingCK>(real_body));
    }
    return relaxation_scaling;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addRelaxationPositionUpdate(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &position_update = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        position_update.add(&main_methods.template addStateDynamics<PositionRelaxationCK>(real_body));
        if (body_config.with_level_set_)
        {
            auto &near_body_surface = entity_manager.getEntityByName<NearShapeSurface>(body_config.name_);
            position_update.add(&main_methods.template addStateDynamics<LevelsetBounding>(near_body_surface));
        }
    }
    return position_update;
}
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addBodyNormalDirection(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager, MethodContainerType &main_methods)
{
    ParticleDynamicsGroup &normal_direction_update = main_methods.addParticleDynamicsGroup();
    for (const auto &body_config : bodies_config_.relaxation_bodies_)
    {
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_config.name_);
        if (body_config.is_solid_body_)
        {
            normal_direction_update.add(&main_methods.template addStateDynamics<NormalFromBodyShapeCK>(real_body));
            real_body.getBaseParticles().template addEvolvingVariable<Vecd>("NormalDirection");
        }
    }
    return normal_direction_update;
}
//=================================================================================================//
template <class AlignedBoxPartType, class ConstraintMethodType>
class AlignedBoxConstraint : public BaseLocalDynamics<AlignedBoxPartType>
{
    using DataType = typename ConstraintMethodType::ReturnType;

  public:
    template <typename... Args>
    AlignedBoxConstraint(AlignedBoxPartType &aligned_box_part, const std::string &variable_name, Args &&...args)
        : BaseLocalDynamics<AlignedBoxPartType>(aligned_box_part),
          sv_aligned_box_(aligned_box_part.svAlignedBox()),
          dv_variable_(this->particles_->template getVariableByName<DataType>(variable_name)),
          constraint_(std::forward<Args>(args)...){};

    class UpdateKernel
    {
        DataType *variable_;

      public:
        template <class ExecutionPolicy, class EncloserType>
        UpdateKernel(const ExecutionPolicy &ex_policy, EncloserType &encloser)
            : aligned_box_(encloser.sv_aligned_box_->DelegatedData(ex_policy)),
              constraint_(encloser.constraint_){};
        void update(size_t index_i, Real dt = 0.0)
        {
            variable_[index_i] = constraint_(aligned_box_->getTransform(), variable_[index_i]);
        };

      protected:
        AlignedBox *aligned_box_;
        ConstraintMethodType constraint_;
    };

  protected:
    SingularVariable<AlignedBox> *sv_aligned_box_;
    DiscreteVariable<DataType> *dv_variable_;
    ConstraintMethodType constraint_;
};

class ConstraintVectorAxis : public ReturnFunction<Vecd>
{
    int axis_;
    Vecd constraint_value_;

  public:
    ConstraintVectorAxis(int axis) : constraint_value_(Vecd::Zero()), axis_(axis) {};

    Vecd operator()(const Transform &transform, const Vecd &variable)
    {
        Vecd frame_variable = transform.shiftBaseStationToFrame(variable);
        frame_variable[axis_] = constraint_value_[axis_];
        return transform.xformFrameVecToBase(frame_variable);
    };
};
//=================================================================================================//
template <class MethodContainerType>
ParticleDynamicsGroup &ParticleRelaxation::addRelaxationConstraints(
    RelaxationSystem &relaxation_system, EntityManager &entity_manager,
    MethodContainerType &main_methods, const json &config)
{
    ParticleDynamicsGroup &relaxation_constraints = main_methods.addParticleDynamicsGroup();
    for (const auto &rc : config)
    {
        const std::string body_name = rc.at("body_name").get<std::string>();
        RealBody &real_body = relaxation_system.getBodyByName<RealBody>(body_name);
        AlignedBox &constraint_region = entity_manager.getEntityByName<
            AlignedBox>(rc.at("aligned_box").get<std::string>());
        auto &body_part = real_body.addBodyPart<AlignedBoxByParticle>(constraint_region);
        std::string type = rc.at("type").get<std::string>();
        if (type == "normal")
        {
            relaxation_constraints.add(
                &main_methods.template addStateDynamics<AlignedBoxConstraint, ConstraintVectorAxis>(
                    body_part, "KernelGradientIntegral", 0));
        }
        else
        {
            relaxation_constraints.add(
                &main_methods.template addStateDynamics<ConstantConstraintCK, Vecd>(
                    real_body, "KernelGradientIntegral", Vecd::Zero()));
        }
    }
    return relaxation_constraints;
}
//=================================================================================================//
} // namespace SPH
#endif // PARTICLE_RELAXATION_BUILDER_HPP
