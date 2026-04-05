#include "sph_simulation_utility.h"

namespace SPH
{
//=================================================================================================//
Vecd jsonToVecd(const nlohmann::json &arr)
{
    Vecd v = Vecd::Zero();
    const int dim = static_cast<int>(Vecd::RowsAtCompileTime);
    for (int i = 0; i < std::min(dim, static_cast<int>(arr.size())); ++i)
        v[i] = arr[i].get<Real>();
    return v;
}
//=================================================================================================//
#ifdef SPHINXSYS_2D
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>());
    Vec2d translation = jsonToVecd(config.at("translation"));
    return Transform(rotation, translation);
}
#else
Transform jsonToTransform(const nlohmann::json &config)
{
    Rotation rotation(config.at("rotation_angle").get<Real>(),
                      jsonToVecd(config.at("rotation_axis")));
    Vec3d translation = jsonToVecd(config.at("translation"));
    return Transform(rotation, translation);
}
#endif
//=================================================================================================//
} // namespace SPH
