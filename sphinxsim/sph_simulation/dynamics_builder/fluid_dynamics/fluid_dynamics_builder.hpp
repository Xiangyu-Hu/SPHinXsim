#ifndef FLUID_DYNAMICS_BUILDER_HPP
#define FLUID_DYNAMICS_BUILDER_HPP

#include "fluid_dynamics_builder.h"
#include "sph_simulation.h"

namespace SPH
{
//=================================================================================================//
using namespace fluid_dynamics;
//=================================================================================================//
template <class FluidType, class InnerRelationType, class ContactRelationType>
BaseDynamics<void> &FluidDynamicsBuilder::buildDensityRegularization(
    SPHSimulation &sim, MainMethods &main_methods, InnerRelationType &inner_relation,
    ContactRelationType &contact_relation, const std::string &surface_type)
{
    auto &density_summation =
        main_methods.template addInteractionDynamics<CompressionSummation>(inner_relation)
            .addPostContactInteraction(contact_relation);

    auto &initialization_pipeline = sim.getInitializationPipeline();
    SPHBody &sph_body = inner_relation.getSPHBody();

    auto &average_compression = main_methods.template addReduceDynamics<AverageCompression>(sph_body);
    initialization_pipeline.insert_hook(
        InitializationHookPoint::InitialCondition, [&]()
        { 
            density_summation.exec();
            Real average_compression_value = average_compression.exec();
            std::cout << "\n------------------------------------------------------------" << std::endl;
            std::cout << "FluidDynamicsBuilder::buildDensityRegularization : " 
                      << "Initial average compression of FluidBody '" << sph_body.Name() 
                      << "' is " << average_compression_value << std::endl; 
            std::cout << "------------------------------------------------------------" << std::endl; });

    auto &minimum_compression =
        main_methods.template addReduceDynamics<
            QuantityReduce, IndexedMin, SimpleEvaluation<IndexedValue<Real>>>(sph_body, "Compression");
    auto &maximum_compression =
        main_methods.template addReduceDynamics<
            QuantityReduce, IndexedMax, SimpleEvaluation<IndexedValue<Real>>>(sph_body, "Compression");

    initialization_pipeline.insert_hook(
        InitializationHookPoint::PreSimulationSanityCheck, [&]()
        { 
            auto lower_limit = minimum_compression.exec();
            auto upper_limit = maximum_compression.exec();
            if (lower_limit.first < 0.95 || upper_limit.first > 1.05 ||
                std::isnan(lower_limit.first) || std::isnan(upper_limit.first))
            {
                std::cout << "\n------------------------------------------------------------" << std::endl;
                std::cout << "Error: Compression is out of range!" << std::endl;
                std::cout << "Lower limit: " << lower_limit.first << " at particle " << lower_limit.second << std::endl;
                std::cout << "Upper limit: " << upper_limit.first << " at particle " << upper_limit.second << std::endl;
                std::cout << "The possible issues are the following:" << std::endl;
                std::cout << "- Too large: overlapped bodies" << std::endl;
                std::cout << "- Too small: insufficient resolution due to thin layer" << std::endl;
                std::cout << "------------------------------------------------------------" << std::endl;
                exit(1);
            } });

    auto &density_regularization = main_methods.addParticleDynamicsGroup();
    density_regularization.add(&density_summation);

    if (surface_type == "confined")
    {
        density_regularization.add(
            &main_methods.template addStateDynamics<
                DensityRegularization, FluidType, Internal>(sph_body));
        return density_regularization;
    }

    if (surface_type == "free_surface")
    {
        density_regularization.add(
            &main_methods.template addStateDynamics<
                DensityRegularization, FluidType, FreeSurface>(sph_body));
        return density_regularization;
    }

    if (surface_type == "open_boundary")
    {
        density_regularization.add(
            &main_methods.template addStateDynamics<
                DensityRegularization, FluidType, Internal, ExcludeBufferParticles>(sph_body));
        return density_regularization;
    }

    if (surface_type == "free_stream")
    {
        density_regularization.add(
            &main_methods.template addStateDynamics<
                DensityRegularization, FluidType, FreeStream>(sph_body));
        return density_regularization;
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::buildDensityRegularization: no supported surface type found!");
}
//=================================================================================================//
template <template <typename...> class AcousticHalfStepForOneBodyType, class InnerRelationType>
BaseDynamics<void> &FluidDynamicsBuilder::addAcousticHalfStepForOneBody(
    SPHSimulation &sim, InnerRelationType &inner_relation, MainMethods &main_methods)
{
    auto &config_manager = sim.getConfigManager();
    auto &sph_body = inner_relation.getSPHBody();
    std::string body_name = sph_body.Name();
    auto &fluid_solver_config = config_manager.getEntity<FluidSolverConfig>("FluidSolverConfig");

    if (sph_body.template isMatterMaterial<WeaklyCompressibleFluid>())
    {
        using RiemannSolverType =
            RiemannSolver<WeaklyCompressibleFluid, WeaklyCompressibleFluid, TruncatedLinear>;
        std::string kernel_correction = fluid_solver_config.kernel_correction_;

        if (kernel_correction == "none")
        {
            auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
                AcousticHalfStepForOneBodyType, RiemannSolverType, NoKernelCorrectionCK>(inner_relation);

            addAcousticHalfStepWithSolidBodies<RiemannSolverType, NoKernelCorrectionCK>(
                sim, complex_dynamics, body_name);

            return complex_dynamics;
        }
        else
        {
            auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
                AcousticHalfStepForOneBodyType, RiemannSolverType, LinearCorrectionCK>(inner_relation);

            addAcousticHalfStepWithSolidBodies<RiemannSolverType, LinearCorrectionCK>(
                sim, complex_dynamics, body_name);
            return complex_dynamics;
        }
    }

    if (sph_body.template isMatterMaterial<WeaklyCompressibleMixture>())
    {
        using RiemannSolverType =
            RiemannSolver<WeaklyCompressibleMixture, WeaklyCompressibleMixture, TruncatedLinear>;

        auto &complex_dynamics = main_methods.template addInteractionDynamicsOneLevel<
            AcousticHalfStepForOneBodyType, RiemannSolverType, LinearCorrectionCK>(inner_relation);

        addAcousticHalfStepWithSolidBodies<RiemannSolverType, LinearCorrectionCK>(
            sim, complex_dynamics, body_name);

        return complex_dynamics;
    }

    throw std::runtime_error(
        "FluidDynamicsBuilder::addAcousticHalfStepForOneBody: no supported material type found!");
}
//=================================================================================================//
template <class RiemannSolverType, class KernelCorrectionType, class AcousticHalfStepType>
void FluidDynamicsBuilder::addAcousticHalfStepWithSolidBodies(
    SPHSimulation &sim, AcousticHalfStepType &complex_dynamics, std::string body_name)
{
    auto &sph_system = sim.getSPHSystem();
    auto &config_manager = sim.getConfigManager();
    if (config_manager.hasEntity<SPHBodiesConfig>("SolidBodiesConfig"))
    {
        auto &solid_bodies_config = config_manager.getEntity<SPHBodiesConfig>("SolidBodiesConfig");
        for (const auto &sb_tgt : solid_bodies_config)
        {
            std::string relation_name = body_name + sb_tgt->name_;
            auto &contact_relation = sph_system.getRelationByName<
                Contact<Relation<FluidBody, SolidBody>>>(relation_name);
            complex_dynamics.template addPostContactInteraction<
                Wall, RiemannSolverType, KernelCorrectionType>(contact_relation);
        }
    }
}
//=================================================================================================//
} // namespace SPH
#endif // FLUID_DYNAMICS_BUILDER_HPP
