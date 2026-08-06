#include "geometry_builder.hpp"

namespace SPH
{
//=================================================================================================//
#ifdef SPHINXSYS_2D
//=================================================================================================//
Rotation getRotationFromXAxis(const Vecd &direction)
{
    Real angle = std::atan2(direction[yAxis], direction[xAxis]);
    return Rotation(angle);
}
//=================================================================================================//
#else
Rotation getRotationFromXAxis(const Vecd &direction)
{
    Vec3d rotation_axis = Vec3d::UnitX().cross(direction);
    Real rotation_angle = std::acos(Vec3d::UnitX().dot(direction));
    return Rotation(rotation_angle, rotation_axis);
}
#endif
//=================================================================================================//
GeometryBuilder::GeometryBuilder(const fs::path &config_path)
    : config_path_(config_path)
{
    IO::initEnvironment();
}
//=================================================================================================//
GeometryBuilder::~GeometryBuilder() = default;
//=================================================================================================//
void GeometryBuilder::resetInOutputRoot(const fs::path &output_root)
{
    IOEnvironment &io_env = IO::getEnvironment();
    if (!fs::exists(output_root))
    {
        fs::create_directories(output_root);
    }
    io_env.resetOutputFolder((output_root / "output").string(), true);
    io_env.resetInputFolder((config_path_.parent_path()).string(), true);
}
//=================================================================================================//
void GeometryBuilder::buildGeometries()
{
    json config = loadConfig();
    config_manager_.clear();
    config_manager_.emplaceEntity<ScalingConfig>("ScalingConfig", config);
    createGeometries(config_manager_, config.at("geometries"));
}
//=================================================================================================//
std::map<std::string, std::pair<std::vector<double>, std::vector<double>>> GeometryBuilder::getShapeBounds()
{
    return getShapeBoundsFromConfigManager(config_manager_);
}
//=================================================================================================//
std::map<std::string, std::pair<std::vector<double>, std::vector<double>>>
GeometryBuilder::getShapeBoundsFromConfigManager(EntityManager &config_manager)
{
    std::map<std::string, std::pair<std::vector<double>, std::vector<double>>> result;
    for (Shape *shape : config_manager.entitiesWith<Shape>())
    {
        BoundingBoxd bounds = shape->getBounds();
        std::vector<double> lower(bounds.lower_.data(), bounds.lower_.data() + bounds.lower_.size());
        std::vector<double> upper(bounds.upper_.data(), bounds.upper_.data() + bounds.upper_.size());
        result[shape->Name()] = {lower, upper};
    }
    return result;
}
//=================================================================================================//
json GeometryBuilder::loadConfig()
{
    json config;
    std::ifstream file(config_path_);
    if (!file.is_open())
    {
        throw std::runtime_error(
            "GeometryBuilder::loadConfig: unable to open config file " + config_path_.string());
    }
    file >> config;
    return config;
}
//=================================================================================================//
void SystemDomainConfig::updateSystemDomain(const BoundingBoxd &shape_bounds)
{
    system_bounds_ = system_bounds_.add(shape_bounds);
}
//=================================================================================================//
void GeometryBuilder::createGeometries(EntityManager &config_manager, const json &config)
{
    auto &scaling_config = config_manager.getEntity<ScalingConfig>("ScalingConfig");
    Real scaling_factor = scaling_config.getScalingRef("Length");
    SystemDomainConfig *system_domain_config = config_manager.emplaceEntity<
        SystemDomainConfig>("SystemDomainConfig", parseSystemDomainConfig(scaling_config, config));

    if (config.contains("primitives"))
    {
        for (const auto &primitive : config.at("primitives"))
        {
            addPrimitive(scaling_config, config_manager, primitive);
        }
    }

    for (const auto &geo : config.at("shapes"))
    {
        Shape *shape = addShape(scaling_config, config_manager, geo);
        config_manager.addEntity<Shape>(shape->Name(), shape);
        system_domain_config->updateSystemDomain(shape->getBounds());
    }

    if (config.contains("oriented_boxes"))
    {
        for (const auto &ab : config.at("oriented_boxes"))
        {
            GeometricShapeBox oriented_box_shape = addOrientedBox(scaling_config, config_manager, ab);
            oriented_box_shape.writeGeometricShapeBoxToVtp(scaling_factor);
        }
    }
}
//=================================================================================================//
void GeometryBuilder::addPrimitive(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
{
    std::string name = config.at("name").get<std::string>();
    std::string type = config.at("type").get<std::string>();

    if (type == "box")
    {
        TransformGeometryBox box = parseBox(scaling_config, config);
        config_manager.emplaceEntity<TransformGeometryBox>(name, box);
        return;
    }

    throw std::runtime_error(
        "GeometryBuilder::addPrimitive: unsupported primitive type: " + type);
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
TransformGeometryBox GeometryBuilder::fetch_or_parseBox(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
{
    if (config.contains("primitive"))
    {
        return TransformGeometryBox(config_manager.getEntity<TransformGeometryBox>(
            config.at("primitive").get<std::string>()));
    }
    else
    {
        return parseBox(scaling_config, config);
    }
}
//=================================================================================================//
#ifdef SPHINXSYS_3D
TransformGeometryCylinder GeometryBuilder::fetch_or_parseCylinder(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
{
    if (config.contains("primitive"))
    {
        return TransformGeometryCylinder(config_manager.getEntity<TransformGeometryCylinder>(
            config.at("primitive").get<std::string>()));
    }
    else
    {
        Real radius = scaling_config.jsonToReal(config.at("radius"), "Length");
        Real half_height = scaling_config.jsonToReal(config.at("half_height"), "Length");
        Transform transform = scaling_config.jsonToTransform(config.at("transform"));
        return TransformGeometryCylinder(transform, radius, half_height);
    }
}
#endif
//=================================================================================================//
SystemDomainConfig GeometryBuilder::parseSystemDomainConfig(
    const ScalingConfig &scaling_config, const json &config)
{
    SystemDomainConfig system_config;
    if (config.contains("system_domain"))
    {
        system_config.system_bounds_ = parseBoundingBox(scaling_config, config.at("system_domain"));
    }
    system_config.particle_spacing_ = parseGlobalResolution(scaling_config, config.at("global_resolution"));
    return system_config;
}
//=================================================================================================//
Real GeometryBuilder::parseGlobalResolution(const ScalingConfig &scaling_config, const json &config)
{
    if (config.contains("particle_spacing"))
    {
        return scaling_config.jsonToReal(config.at("particle_spacing"), "Length");
    }
    else
    {
        if (config.contains("characteristic_length_particles"))
        {
            UnsignedInt num_particles = config.at("characteristic_length_particles").get<UnsignedInt>();
            return 1.0 / Real(num_particles);
        }
    }

    throw std::runtime_error(
        "GeometryBuilder::parseGlobalResolution: global resolution is not specified in the config.");
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
MultiPolygon GeometryBuilder::parseMultiPolygon(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
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

    if (polygon_type == "box")
    {
        TransformGeometryBox box = fetch_or_parseBox(scaling_config, config_manager, config);
        multi_polygon.addBox(box.getTransform(), box.HalfSize(), GeometricOps::add);
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

    if (polygon_type == "circle")
    {
        Vecd center = scaling_config.jsonToVecd(config.at("center"), "Length");
        Real radius = scaling_config.jsonToReal(config.at("radius"), "Length");
        int resolution = config.at("resolution").get<int>();
        multi_polygon.addCircle(center, radius, resolution, GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "triangle")
    {
        Transform transform = scaling_config.jsonToTransform(config.at("transform"));
        Vecd half_size = scaling_config.jsonToVecd(config.at("half_size"), "Length");
        multi_polygon.addTriangle(transform, half_size, GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "clockwise_points")
    {
        std::vector<Vecd> points;
        for (const auto &p : config.at("points"))
        {
            points.push_back(scaling_config.jsonToVecd(p, "Length"));
        }

        if ((points[0] - points.back()).norm() > Eps)
        {
            throw std::runtime_error(
                "GeometryBuilder::parseMultiPolygon: the first and last of clockwise points must be the same!");
        }
        multi_polygon.addPolygon(points, GeometricOps::add);
        return multi_polygon;
    }

    if (polygon_type == "data_file")
    {
        multi_polygon.addPolygonFromFile(
            config.at("file_name").get<std::string>(), GeometricOps::add,
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
        TransformGeometryBox box = fetch_or_parseBox(scaling_config, config_manager, config);
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

    if (type == "extrude_shape")
    {
        const std::string original_name = config.at("original").get<std::string>();
        Shape &base_shape = config_manager.getEntity<Shape>(original_name);

        Real thickness = 0.0;
        if (config.at("thickness").is_number_float())
        {
            thickness = scaling_config.jsonToReal(config.at("thickness"), "Length");
        }
        else if (config.at("thickness").is_string())
        {
            std::string thickness_name = config.at("thickness").get<std::string>();
            if (thickness_name == "boundary")
            {
                SystemDomainConfig &system_domain_config =
                    config_manager.getEntity<SystemDomainConfig>("SystemDomainConfig");
                thickness = 4.0 * system_domain_config.particle_spacing_;
            }
            else
            {
                throw std::runtime_error(
                    "GeometryBuilder::addShape: unsupported thickness type for extrude_shape: " + thickness_name);
            }
        }
        return config_manager.emplaceEntity<ExtrudeShape>(name, base_shape, thickness);
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
            multi_polygon.addMultiPolygon(parseMultiPolygon(scaling_config, config_manager, plg), op);
        }
        MultiPolygonShape *shape = config_manager.emplaceEntity<MultiPolygonShape>(name, multi_polygon, name);
        shape->writeMultiPolygonShapeToVtp(scaling_factor);
        return shape;
    }
#else
    if (type == "cylinder")
    {
        TransformGeometryCylinder cylinder = fetch_or_parseCylinder(scaling_config, config_manager, config);
        GeometricShapeCylinder *shape = config_manager.emplaceEntity<GeometricShapeCylinder>(name, cylinder, name);
        shape->writeGeometricShapeCylinderToVtp(scaling_factor);
        return shape;
    }

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
            name, config.at("file_name").get<std::string>(), translation, scale, name);
        shape->writTriangleMeshShapeToVtp(Transform(), scaling_factor);
        return shape;
    }
#endif

    throw std::runtime_error("GeometryBuilder::addShape: unsupported shape: " + type);
}
//=================================================================================================//
GeometricShapeBox GeometryBuilder::addOrientedBox(
    const ScalingConfig &scaling_config, EntityManager &config_manager, const json &config)
{
    const std::string name = config.at("name").get<std::string>();
    const std::string type = config.at("type").get<std::string>();

    if (type == "boundary")
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
        OrientedBox *oriented_box = config_manager.emplaceEntity<OrientedBox>(
            name, xAxis, Transform(rotation, translation), half_size);
        return GeometricShapeBox(*oriented_box, name); // for visualization only
    }

    if (type == "region")
    {
        OrientedBox *oriented_box = config_manager.emplaceEntity<OrientedBox>(
            name, xAxis, fetch_or_parseBox(scaling_config, config_manager, config));
        return GeometricShapeBox(*oriented_box, name); // for visualization only
    }

    throw std::runtime_error("GeometryBuilder::addOrientedBox: unsupported oriented box type: " + type);
}
//=================================================================================================//
} // namespace SPH
