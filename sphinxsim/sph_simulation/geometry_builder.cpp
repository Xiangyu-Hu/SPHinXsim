#include "geometry_builder.hpp"

namespace SPH
{
//=================================================================================================//
void GeometryBuilder::createGeometries(EntityManager &entity_manager, const json &config)
{
    entity_manager.emplaceEntity<SystemDomainConfig>(
        "SystemDomainConfig", parseSystemDomainConfig(config));
    for (const auto &geo : config.at("shapes"))
    {
        Shape *shape = addGeometry(entity_manager, geo);
        entity_manager.addEntity<Shape>(shape->getName(), shape);
    }
}
//=================================================================================================//
BoundingBoxd GeometryBuilder::parseBoundingBox(const json &config)
{
    Vecd lower_bound = jsonToVecd(config.at("lower_bound"));
    Vecd upper_bound = jsonToVecd(config.at("upper_bound"));
    return BoundingBoxd(lower_bound, upper_bound);
}
//=================================================================================================//
TransformGeometryBox GeometryBuilder::parseBox(const json &config)
{
    Vecd half_size = jsonToVecd(config.at("half_size"));
    Transform transform = jsonToTransform(config.at("transform"));
    return TransformGeometryBox(transform, half_size);
}
//=================================================================================================//
SystemDomainConfig GeometryBuilder::parseSystemDomainConfig(const json &config)
{
    SystemDomainConfig system_config;
    system_config.system_domain_bounds_ = parseBoundingBox(config.at("system_domain"));
    system_config.particle_spacing_ = config.at("global_resolution").get<Real>();
    return system_config;
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
MultiPolygon GeometryBuilder::parseMultiPolygon(const json &config)
{
    MultiPolygon multi_polygon;
    const std::string polygon_type = config.at("type").get<std::string>();
    if (polygon_type == "bounding_box")
    {
        Vecd lower_bound = jsonToVecd(config.at("lower_bound"));
        Vecd upper_bound = jsonToVecd(config.at("upper_bound"));
        multi_polygon.addBox(BoundingBoxd(lower_bound, upper_bound), GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "container_box")
    {
        BoundingBoxd inner_box(
            jsonToVecd(config.at("inner_lower_bound")), jsonToVecd(config.at("inner_upper_bound")));
        Real thickness = config.at("thickness").get<Real>();
        multi_polygon.addContainerBox(inner_box, thickness, GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "data_file")
    {
        multi_polygon.addPolygonFromFile(config.at("file_path").get<std::string>(), GeometricOps::add);
        return multi_polygon;
    }

    throw std::runtime_error("SPHSimulation::addShape: unsupported polygon type: " + polygon_type);
}
#endif
//=================================================================================================//
Shape *GeometryBuilder::addGeometry(EntityManager &entity_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    const std::string type = config.at("type").get<std::string>();

    if (type == "box")
    {
        TransformGeometryBox box = parseBox(config);
        GeometricShapeBox *shape = entity_manager.emplaceEntity<GeometricShapeBox>(name, box, name);
        return shape;
    }

    if (type == "bounding_box")
    {
        BoundingBoxd bounding_box = parseBoundingBox(config);
        GeometricShapeBox *shape = entity_manager.emplaceEntity<GeometricShapeBox>(name, bounding_box, name);
        return shape;
    }

    if (type == "container_box")
    {
        BoundingBoxd inner_box(jsonToVecd(config.at("inner_lower_bound")),
                               jsonToVecd(config.at("inner_upper_bound")));
        BoundingBoxd outer_box = inner_box.expand(config.at("thickness").get<Real>());
        ComplexShape *shape = entity_manager.emplaceEntity<ComplexShape>(name, name);
        shape->add<GeometricShapeBox>(outer_box);
        shape->subtract<GeometricShapeBox>(inner_box);
        return shape;
    }

#ifdef SPHINXSYS_2D
    if (type == "multipolygon")
    {
        MultiPolygon multi_polygon;
        for (const auto &plg : config.at("polygons"))
        {
            const std::string operation_name = plg.at("operation").get<std::string>();
            GeometricOps op = parseGeometricOp(operation_name);
            multi_polygon.addMultiPolygon(parseMultiPolygon(plg), op);
        }
        MultiPolygonShape *shape = entity_manager.emplaceEntity<MultiPolygonShape>(name, multi_polygon, name);
        return shape;
    }
#endif

    throw std::runtime_error("GeometryBuilder::addGeometry: unsupported geometry: " + type);
}
//=================================================================================================//
} // namespace SPH
