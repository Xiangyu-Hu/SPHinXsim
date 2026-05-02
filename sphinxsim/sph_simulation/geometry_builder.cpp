#include "geometry_builder.hpp"

namespace SPH
{
//=================================================================================================//
void SystemDomainConfig::updateSystemDomainConfig(const BoundingBoxd &shape_bounds)
{
    system_bounds_ = system_bounds_.add(shape_bounds);
    updateParticleSpacing();
}
//=================================================================================================//
void SystemDomainConfig::updateParticleSpacing()
{
    if (!prescribed_spacing_)
    {
        particle_spacing_ = system_bounds_.MinimumDimension() /
                            Real(min_dimension_particles_);
    }
}
//=================================================================================================//
void GeometryBuilder::createGeometries(EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    Real scaling_factor = scaling_config.getScalingRef("Length");
    SystemDomainConfig *system_domain_config = config_manager.emplaceEntity<
        SystemDomainConfig>("SystemDomainConfig", parseSystemDomainConfig(scaling_config, config));
    for (const auto &geo : config.at("shapes"))
    {
        Shape *shape = addShape(scaling_config, config_manager, geo);
        config_manager.addEntity<Shape>(shape->getName(), shape);
        system_domain_config->updateSystemDomainConfig(shape->getBounds());
    }

    if (config.contains("aligned_boxes"))
    {
        for (const auto &ab : config.at("aligned_boxes"))
        {
            GeometricShapeBox aligned_box_shape = addAlignedBox(scaling_config, config_manager, ab);
            aligned_box_shape.writeGeometricShapeBoxToVtp(scaling_factor);
        }
    }
}
//=================================================================================================//
BoundingBoxd GeometryBuilder::parseBoundingBox(const ScalingConfig &scaling_config, const json &config)
{
    Vecd lower_bound = scaling_config.jsonToVecd(config.at("lower_bound"), "Length");
    Vecd upper_bound = scaling_config.jsonToVecd(config.at("upper_bound"), "Length");
    return BoundingBoxd(lower_bound, upper_bound);
}
//=================================================================================================//
TransformGeometryBox GeometryBuilder::parseBox(const ScalingConfig &scaling_config, const json &config)
{
    Vecd half_size = scaling_config.jsonToVecd(config.at("half_size"), "Length");
    Transform transform = scaling_config.jsonToTransform(config.at("transform"));
    return TransformGeometryBox(transform, half_size);
}
//=================================================================================================//
SystemDomainConfig GeometryBuilder::parseSystemDomainConfig(
    const ScalingConfig &scaling_config, const json &config)
{
    SystemDomainConfig system_config;
    if (config.contains("system_domain"))
    {
        system_config.system_bounds_ = parseBoundingBox(scaling_config, config.at("system_domain"));
    }
    if (config.contains("global_resolution"))
    {
        parseGlobalResolution(scaling_config, system_config, config.at("global_resolution"));
    }
    return system_config;
}
//=================================================================================================//
void GeometryBuilder::parseGlobalResolution(
    const ScalingConfig &scaling_config, SystemDomainConfig &system_config, const json &config)
{
    if (config.contains("min_dimension_particles"))
    {
        system_config.prescribed_spacing_ = false;
        system_config.min_dimension_particles_ =
            config.at("min_dimension_particles").get<UnsignedInt>();
        system_config.updateParticleSpacing();
        return;
    }
    system_config.particle_spacing_ = scaling_config.jsonToReal(config.at("particle_spacing"), "Length");
}
//=================================================================================================//
GeometricOps GeometryBuilder::parseGeometricOp(const std::string &op_str)
{
    if (op_str == "union")
        return GeometricOps::add;
    if (op_str == "intersection")
        return GeometricOps::intersect;
    if (op_str == "subtraction")
        return GeometricOps::sub;

    throw std::runtime_error("GeometryBuilder::parseGeometricOp: unsupported geometric operation: " + op_str);
}
//=================================================================================================//
#ifdef SPHINXSYS_2D
MultiPolygon GeometryBuilder::parseMultiPolygon(const ScalingConfig &scaling_config, const json &config)
{
    MultiPolygon multi_polygon;
    const std::string polygon_type = config.at("type").get<std::string>();
    if (polygon_type == "bounding_box")
    {
        Vecd lower_bound = scaling_config.jsonToVecd(config.at("lower_bound"), "Length");
        Vecd upper_bound = scaling_config.jsonToVecd(config.at("upper_bound"), "Length");
        multi_polygon.addBox(BoundingBoxd(lower_bound, upper_bound), GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "container_box")
    {
        BoundingBoxd inner_box(
            scaling_config.jsonToVecd(config.at("inner_lower_bound"), "Length"),
            scaling_config.jsonToVecd(config.at("inner_upper_bound"), "Length"));
        Real thickness = scaling_config.jsonToReal(config.at("thickness"), "Length");
        multi_polygon.addContainerBox(inner_box, thickness, GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "data_file")
    {
        multi_polygon.addPolygonFromFile(config.at("file_path").get<std::string>(), GeometricOps::add,
                                         Vecd::Zero(), 1.0 / scaling_config.getScalingRef("Length"));
        return multi_polygon;
    }

    throw std::runtime_error("SPHSimulation::parseMultiPolygon: unsupported polygon type: " + polygon_type);
}
#endif
//=================================================================================================//
Shape *GeometryBuilder::addShape(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
{

    Real scaling_factor = scaling_config.getScalingRef("Length");
    const std::string name = config.at("name").get<std::string>();
    const std::string type = config.at("type").get<std::string>();

    if (type == "box")
    {
        TransformGeometryBox box = parseBox(scaling_config, config);
        GeometricShapeBox *shape = config_manager.emplaceEntity<GeometricShapeBox>(name, box, name);
        shape->writeGeometricShapeBoxToVtp(scaling_factor);
        return shape;
    }

    if (type == "bounding_box")
    {
        BoundingBoxd bounding_box = parseBoundingBox(scaling_config, config);
        config_manager.emplaceEntity<BoundingBoxd>(name, bounding_box);
        GeometricShapeBox *shape = config_manager.emplaceEntity<GeometricShapeBox>(name, bounding_box, name);
        shape->writeGeometricShapeBoxToVtp(scaling_factor);
        return shape;
    }

    if (type == "expanded_box")
    {
        const std::string original_name = config.at("original").get<std::string>();
        TransformGeometryBox expand_box =
            config_manager.getEntity<GeometricShapeBox>(original_name)
                .getExpandedBox(scaling_config.jsonToReal(config.at("expansion"), "Length"));
        GeometricShapeBox *shape = config_manager.emplaceEntity<GeometricShapeBox>(name, expand_box, name);
        shape->writeGeometricShapeBoxToVtp(scaling_factor);
        return shape;
    }

    if (type == "complex_shape")
    {
        ComplexShape *complex_shape = config_manager.emplaceEntity<ComplexShape>(name, name);

        StdVec<Shape *> sub_shapes;
        for (const auto &sub_shape_name : config.at("sub_shapes"))
        {
            sub_shapes.push_back(&config_manager.getEntity<Shape>(sub_shape_name));
        }

        for (UnsignedInt i = 0; i < sub_shapes.size(); ++i)
        {
            const auto &operation = config.at("operations").at(i).get<std::string>();
            GeometricOps op = parseGeometricOp(operation);
            if (op != GeometricOps::add && op != GeometricOps::sub)
            {
                throw std::runtime_error(
                    "GeometryBuilder::addShape: unsupported operation for complex shape: " + operation);
            }
            op == GeometricOps::add ? complex_shape->add(sub_shapes[i])
                                    : complex_shape->subtract(sub_shapes[i]);
        }
        return complex_shape;
    }

#ifdef SPHINXSYS_2D
    if (type == "multipolygon")
    {
        MultiPolygon multi_polygon;
        for (const auto &plg : config.at("polygons"))
        {
            const std::string operation_name = plg.at("operation").get<std::string>();
            GeometricOps op = parseGeometricOp(operation_name);
            multi_polygon.addMultiPolygon(parseMultiPolygon(scaling_config, plg), op);
        }
        MultiPolygonShape *shape = config_manager.emplaceEntity<MultiPolygonShape>(name, multi_polygon, name);
        shape->writeMultiPolygonShapeToVtp();
        return shape;
    }
#else
    if (type == "triangle_mesh")
    {
        Vec3d translation = Vec3d::Zero();
        if (config.contains("translation"))
        {
            translation = scaling_config.jsonToVecd(config.at("translation"), "Length");
        }

        Real scale = 1.0;
        if (config.contains("scale"))
        {
            scale = scaling_config.jsonToReal(config.at("scale"), "Dimensionless");
        }

        scale /= scaling_factor;
        TriangleMeshShapeSTL *shape = config_manager.emplaceEntity<TriangleMeshShapeSTL>(
            name, config.at("file_path").get<std::string>(), translation, scale, name);
        shape->writTriangleMeshShapeToVtp(Transform(), scaling_factor);
        return shape;
    }
#endif

    throw std::runtime_error("GeometryBuilder::addShape: unsupported shape: " + type);
}
//=================================================================================================//
GeometricShapeBox GeometryBuilder::addAlignedBox(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    const std::string type = config.at("type").get<std::string>();

    if (type == "in_outlet")
    {
        Vecd center = scaling_config.jsonToVecd(config.at("center"), "Length");
        Vecd normal = scaling_config.jsonToVecd(config.at("normal"), "Dimensionless");
        Real radius = scaling_config.jsonToReal(config.at("radius"), "Length");

        SystemDomainConfig &system_domain_config =
            config_manager.getEntity<SystemDomainConfig>("SystemDomainConfig");
        Real expansion_length = 4.0 * system_domain_config.particle_spacing_;

        Vecd half_size = Vecd::Constant(radius + expansion_length);
        half_size[xAxis] = expansion_length * 0.5;
        Vecd translation = center + normal * half_size[xAxis];
        Rotation rotation = getRotationFromXAxis(normal);
        AlignedBox *aligned_box = config_manager.emplaceEntity<AlignedBox>(
            name, xAxis, Transform(rotation, translation), half_size);
        return GeometricShapeBox(*aligned_box, name); // for visualization only
    }

    if (type == "region")
    {
        AlignedBox *aligned_box = config_manager.emplaceEntity<AlignedBox>(
            name, xAxis, GeometryBuilder::parseBox(scaling_config, config));
        return GeometricShapeBox(*aligned_box, name); // for visualization only
    }

    throw std::runtime_error("GeometryBuilder::addAlignedBox: unsupported aligned box type: " + type);
}
//=================================================================================================//
} // namespace SPH
