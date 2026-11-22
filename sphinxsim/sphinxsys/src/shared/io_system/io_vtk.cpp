#include "io_vtk.hpp"

#include "sph_system.h"
#include "vector_functions.h"

#include <limits>

namespace SPH
{
//=============================================================================================//
void BodyStatesRecordingToVtp::setVariableScalingReference(
    const std::string &body_name, const std::string &variable_name, Real reference)
{
    scaling_references_[body_name][variable_name] = reference;
}
//=============================================================================================//
std::map<std::string, Real> BodyStatesRecordingToVtp::outputScalingReferences(
    const std::string &body_name, BaseParticles &particles) const
{
    std::map<std::string, Real> result;
    auto body_refs = scaling_references_.find(body_name);
    if (body_refs == scaling_references_.end())
        return result;

    auto position_ref = body_refs->second.find("Position");
    if (position_ref != body_refs->second.end())
        result.emplace("Position", position_ref->second);

    auto &variables = particles.ParticleAttributesToWrite();
    auto add_references = [&](const auto &typed_variables, bool split_entries)
    {
        for (auto *variable : typed_variables)
        {
            auto reference = body_refs->second.find(variable->Name());
            if (reference == body_refs->second.end())
                continue;
            if (split_entries)
            {
                for (UnsignedInt k = 0; k != variable->getWidth(); ++k)
                    result.emplace(variable->Name() + variable->getEntryName(k), reference->second);
            }
            else
                result.emplace(variable->Name(), reference->second);
        }
    };
#ifdef SPHINXSYS_USE_VTK
    constexpr bool split_entries = false;
#else
    constexpr bool split_entries = true;
#endif
    add_references(std::get<DataTypeIndex<Real>::value>(variables), split_entries);
    add_references(std::get<DataTypeIndex<Vecd>::value>(variables), split_entries);
    add_references(std::get<DataTypeIndex<Matd>::value>(variables), false);
    return result;
}
//=============================================================================================//
#ifdef SPHINXSYS_USE_VTK
void BodyStatesRecordingToVtp::writeWithFileName(const std::string &sequence)
{
    for (SPHBody *body : bodies_)
    {
        if (body->checkNewlyUpdated())
        {
            BaseParticles &base_particles = body->getBaseParticles();

            if (state_recording_)
            {
                std::string filefullpath = io_environment_.OutputFolder() + "/" + body->Name() + "_" + sequence + ".vtp";

                size_t total_real_particles = base_particles.TotalRealParticles();

                // Build points
                vtkNew<vtkPoints> points;
                points->SetDataTypeToFloat();
                DiscreteVariable<Vecd> *dv_pos = base_particles.dvParticlePosition();
                for (size_t i = 0; i != total_real_particles; ++i)
                {
                    Vec3d pos = upgradeToVec3d(dv_pos->getValueWithScalingRef(i));
                    points->InsertNextPoint(pos[0], pos[1], pos[2]);
                }

                // Build vertex cells (one vertex per particle)
                vtkNew<vtkCellArray> verts;
                for (vtkIdType i = 0; i < static_cast<vtkIdType>(total_real_particles); ++i)
                {
                    verts->InsertNextCell(1, &i);
                }

                vtkNew<vtkPolyData> polydata;
                polydata->SetPoints(points);
                polydata->SetVerts(verts);

                // Add physical time as field data
                if (sph_system_.isPhysical())
                {
                    vtkNew<vtkDoubleArray> time_array;
                    time_array->SetName("TimeValue");
                    time_array->SetNumberOfTuples(1);
                    time_array->SetValue(0, sv_physical_time_->getValue());
                    polydata->GetFieldData()->AddArray(time_array);
                }

                for (const auto &[name, reference] : outputScalingReferences(body->Name(), base_particles))
                {
                    vtkNew<vtkDoubleArray> reference_array;
                    std::string field_name = "ScalingReference_" + name;
                    reference_array->SetName(field_name.c_str());
                    reference_array->SetNumberOfTuples(1);
                    reference_array->SetValue(0, reference);
                    polydata->GetFieldData()->AddArray(reference_array);
                }

                // Add particle data arrays to point data
                addParticlesToVtkPolyData(polydata, base_particles);

                vtkNew<vtkXMLPolyDataWriter> writer;
                writer->SetInputData(polydata);
                writer->SetFileName(filefullpath.c_str());
                if (binary_output_)
                    writer->SetDataModeToBinary();
                else
                    writer->SetDataModeToAscii();
                writer->Write();
            }
        }
        body->setNotNewlyUpdated();
    }
}
//=============================================================================================//
void BodyStatesRecordingToVtp::addParticlesToVtkPolyData(vtkPolyData *polydata, BaseParticles &particles)
{
    size_t total_real_particles = particles.TotalRealParticles();
    DiscreteVariables &variables_to_write = particles.ParticleAttributesToWrite();

    // Write sorted particle IDs
    vtkNew<vtkIntArray> sorted_id_array;
    sorted_id_array->SetName("SortedParticle_ID");
    sorted_id_array->SetNumberOfValues(static_cast<vtkIdType>(total_real_particles));
    for (size_t i = 0; i < total_real_particles; ++i)
        sorted_id_array->SetValue(static_cast<vtkIdType>(i), static_cast<int>(i));
    polydata->GetPointData()->AddArray(sorted_id_array);

    // Write UnsignedInt variables
    constexpr int type_index_UnsignedInt = DataTypeIndex<UnsignedInt>::value;
    for (DiscreteVariable<UnsignedInt> *variable : std::get<type_index_UnsignedInt>(variables_to_write))
    {
        UnsignedInt *data_field = variable->Data();
        vtkNew<vtkUnsignedIntArray> arr;
        arr->SetName(variable->Name().c_str());
        arr->SetNumberOfValues(static_cast<vtkIdType>(total_real_particles));
        for (size_t i = 0; i < total_real_particles; ++i)
            arr->SetValue(static_cast<vtkIdType>(i), data_field[i]);
        polydata->GetPointData()->AddArray(arr);
    }

    // Write int variables
    constexpr int type_index_int = DataTypeIndex<int>::value;
    for (DiscreteVariable<int> *variable : std::get<type_index_int>(variables_to_write))
    {
        int *data_field = variable->Data();
        vtkNew<vtkIntArray> arr;
        arr->SetName(variable->Name().c_str());
        arr->SetNumberOfValues(static_cast<vtkIdType>(total_real_particles));
        for (size_t i = 0; i < total_real_particles; ++i)
            arr->SetValue(static_cast<vtkIdType>(i), data_field[i]);
        polydata->GetPointData()->AddArray(arr);
    }

    // Write Real (scalar) variables
    constexpr int type_index_Real = DataTypeIndex<Real>::value;
    for (DiscreteVariable<Real> *variable : std::get<type_index_Real>(variables_to_write))
    {
        vtkNew<vtkFloatArray> arr;
        arr->SetName(variable->Name().c_str());
        arr->SetNumberOfValues(static_cast<vtkIdType>(total_real_particles));
        for (size_t i = 0; i < total_real_particles; ++i)
            arr->SetValue(static_cast<vtkIdType>(i), static_cast<float>(variable->getValueWithScalingRef(i)));
        polydata->GetPointData()->AddArray(arr);
    }

    // Write Vecd (vector) variables
    constexpr int type_index_Vecd = DataTypeIndex<Vecd>::value;
    for (DiscreteVariable<Vecd> *variable : std::get<type_index_Vecd>(variables_to_write))
    {
        vtkNew<vtkFloatArray> arr;
        arr->SetName(variable->Name().c_str());
        arr->SetNumberOfComponents(3);
        arr->SetNumberOfTuples(static_cast<vtkIdType>(total_real_particles));
        for (size_t i = 0; i < total_real_particles; ++i)
        {
            Vec3d vec = upgradeToVec3d(variable->getValueWithScalingRef(i));
            float tuple[3] = {static_cast<float>(vec[0]), static_cast<float>(vec[1]), static_cast<float>(vec[2])};
            arr->SetTuple(static_cast<vtkIdType>(i), tuple);
        }
        polydata->GetPointData()->AddArray(arr);
    }

    // Write Matd (matrix) variables
    constexpr int type_index_Matd = DataTypeIndex<Matd>::value;
    for (DiscreteVariable<Matd> *variable : std::get<type_index_Matd>(variables_to_write))
    {
        vtkNew<vtkFloatArray> arr;
        arr->SetName(variable->Name().c_str());
        arr->SetNumberOfComponents(9);
        arr->SetNumberOfTuples(static_cast<vtkIdType>(total_real_particles));
        for (size_t i = 0; i < total_real_particles; ++i)
        {
            Mat3d mat = upgradeToMat3d(variable->getValueWithScalingRef(i));
            float tuple[9];
            for (int k = 0; k < 3; ++k)
            {
                Vec3d col = mat.col(k);
                tuple[3 * k + 0] = static_cast<float>(col[0]);
                tuple[3 * k + 1] = static_cast<float>(col[1]);
                tuple[3 * k + 2] = static_cast<float>(col[2]);
            }
            arr->SetTuple(static_cast<vtkIdType>(i), tuple);
        }
        polydata->GetPointData()->AddArray(arr);
    }
}
#else
void BodyStatesRecordingToVtp::writeWithFileName(const std::string &sequence)
{
    for (SPHBody *body : bodies_)
    {
        if (body->checkNewlyUpdated())
        {
            BaseParticles &base_particles = body->getBaseParticles();

            if (state_recording_)
            {
                std::string filefullpath = io_environment_.OutputFolder() + "/" + body->Name() + "_" + sequence + ".vtp";
                if (fs::exists(filefullpath))
                {
                    fs::remove(filefullpath);
                }
                std::ofstream out_file(filefullpath.c_str(), std::ios::trunc);
                // begin of the XML file
                out_file << "<?xml version=\"1.0\"?>\n";
                out_file << "<VTKFile type=\"PolyData\" version=\"1.0\" byte_order=\"LittleEndian\">\n";
                out_file << " <PolyData>\n";

                auto references = outputScalingReferences(body->Name(), base_particles);
                if (sph_system_.isPhysical() || !references.empty())
                {
                    out_file << "<FieldData>\n";
                    if (sph_system_.isPhysical())
                    {
                        out_file << "<DataArray type=\"Float64\"  Name=\"TimeValue\" NumberOfTuples=\"1\" format=\"ascii\">\n";
                        out_file << std::fixed << std::setprecision(9) << sv_physical_time_->getValueWithScalingRef() << "\n";
                        out_file << " </DataArray>\n";
                    }
                    for (const auto &[name, reference] : references)
                    {
                        std::ostringstream value;
                        value << std::scientific << std::setprecision(std::numeric_limits<Real>::max_digits10)
                              << reference;
                        out_file << "<DataArray type=\"Float64\" Name=\"ScalingReference_" << name
                                 << "\" NumberOfTuples=\"1\" format=\"ascii\">\n";
                        out_file << value.str() << "\n </DataArray>\n";
                    }
                    out_file << "</FieldData>\n";
                }

                size_t total_real_particles = base_particles.TotalRealParticles();
                out_file << "  <Piece Name =\"" << body->Name() << "\" NumberOfPoints=\"" << total_real_particles
                         << "\" NumberOfVerts=\"" << total_real_particles << "\">\n";

                // write current/final particle positions first
                out_file << "   <Points>\n";
                out_file << "    <DataArray Name=\"Position\" type=\"Float32\"  NumberOfComponents=\"3\" format=\"ascii\">\n";
                out_file << "    ";
                DiscreteVariable<Vecd> *dv_pos = base_particles.dvParticlePosition();
                for (size_t i = 0; i != total_real_particles; ++i)
                {
                    Vec3d particle_position = upgradeToVec3d(dv_pos->getValueWithScalingRef(i));
                    out_file << particle_position[0] << " " << particle_position[1] << " " << particle_position[2] << " ";
                }
                out_file << std::endl;
                out_file << "    </DataArray>\n";
                out_file << "   </Points>\n";

                // write header of particles data
                out_file << "   <PointData  Vectors=\"vector\">\n";
                writeParticlesToVtk(out_file, base_particles);
                out_file << "   </PointData>\n";

                // write empty cells
                out_file << "   <Verts>\n";
                out_file << "    <DataArray type=\"Int32\"  Name=\"connectivity\"  format=\"ascii\">\n";
                out_file << "    ";
                for (size_t i = 0; i != total_real_particles; ++i)
                {
                    out_file << i << " ";
                }
                out_file << std::endl;
                out_file << "    </DataArray>\n";
                out_file << "    <DataArray type=\"Int32\"  Name=\"offsets\"  format=\"ascii\">\n";
                out_file << "    ";
                for (size_t i = 0; i != total_real_particles; ++i)
                {
                    out_file << i + 1 << " ";
                }
                out_file << std::endl;
                out_file << "    </DataArray>\n";
                out_file << "   </Verts>\n";

                out_file << "  </Piece>\n";
                out_file << " </PolyData>\n";
                out_file << "</VTKFile>\n";

                out_file.close();
            }
        }
        body->setNotNewlyUpdated();
    }
}
#endif
//=============================================================================================//
void BodyStatesRecordingToVtpString::writeWithFileName(const std::string &sequence)
{
    for (SPHBody *body : bodies_)
    {
        if (body->checkNewlyUpdated())
        {
            if (state_recording_)
            {
                const auto &vtuName = body->Name() + "_" + sequence + ".vtu";
                std::stringstream sstream;
                // begin of the XML file
                writeVtu(sstream, body);
                _vtuData[vtuName] = sstream.str();
            }
        }
        body->setNotNewlyUpdated();
    }
}
//=============================================================================================//
void BodyStatesRecordingToVtpString::writeVtu(std::ostream &stream, SPHBody *body)
{
    stream << "<?xml version=\"1.0\"?>\n";
    stream << "<VTKFile type=\"UnstructuredGrid\" version=\"1.0\" byte_order=\"LittleEndian\">\n";
    stream << " <UnstructuredGrid>\n";

    BaseParticles &base_particles = body->getBaseParticles();
    size_t total_real_particles = base_particles.TotalRealParticles();
    stream << "  <Piece Name =\"" << body->Name() << "\" NumberOfPoints=\"" << total_real_particles << "\" NumberOfCells=\"0\">\n";

    stream << "   <PointData  Vectors=\"vector\">\n";
    writeParticlesToVtk(stream, base_particles);
    stream << "   </PointData>\n";

    // write empty cells
    stream << "   <Cells>\n";
    stream << "    <DataArray type=\"Int32\"  Name=\"connectivity\"  format=\"ascii\">\n";
    stream << "    </DataArray>\n";
    stream << "    <DataArray type=\"Int32\"  Name=\"offsets\"  format=\"ascii\">\n";
    stream << "    </DataArray>\n";
    stream << "    <DataArray type=\"UInt8\"  Name=\"types\"  format=\"ascii\">\n";
    stream << "    </DataArray>\n";
    stream << "   </Cells>\n";

    stream << "  </Piece>\n";

    stream << " </UnstructuredGrid>\n";
    stream << "</VTKFile>\n";
}
//=============================================================================================//
const VtuStringData &BodyStatesRecordingToVtpString::GetVtuData() const
{
    return _vtuData;
}
//=============================================================================================//
WriteToVtpIfVelocityOutOfBound::
    WriteToVtpIfVelocityOutOfBound(SPHSystem &sph_system, Real velocity_bound)
    : BodyStatesRecordingToVtp(sph_system), out_of_bound_(false)
{
    for (size_t i = 0; i < bodies_.size(); ++i)
    {
        check_bodies_.push_back(
            check_bodies_keeper_.createPtr<ReduceDynamics<VelocityBoundCheck>>(*bodies_[i], velocity_bound));
    }
}
//=============================================================================================//
void WriteToVtpIfVelocityOutOfBound::writeWithFileName(const std::string &sequence)
{
    for (auto check_body : check_bodies_)
    {
        out_of_bound_ = out_of_bound_ || check_body->exec();
    }

    if (out_of_bound_)
    {
        BodyStatesRecordingToVtp::writeWithFileName(sequence);
        std::cout << "\n Velocity is out of bound at iteration step " << sequence
                  << "\n The body states have been outputted and the simulation terminates here. \n";
    }
}
//=============================================================================================//
void ParticleGenerationRecordingToVtp::writeWithFileName(const std::string &sequence)
{

    if (state_recording_)
    {
        std::string filefullpath = io_environment_.OutputFolder() + "/" + sph_body_.Name() +
                                   "particle_generation_" + sequence + ".vtp";
        if (fs::exists(filefullpath))
        {
            fs::remove(filefullpath);
        }
        std::ofstream out_file(filefullpath.c_str(), std::ios::trunc);

        size_t total_generated_particles = position_.size();
        // begin of the XML file
        out_file << "<?xml version=\"1.0\"?>\n";
        out_file << "<VTKFile type=\"PolyData\" version=\"1.0\" byte_order=\"LittleEndian\">\n";
        out_file << " <PolyData>\n";

        out_file << "  <Piece Name =\"" << sph_body_.Name() << "\" NumberOfPoints=\"" << total_generated_particles
                 << "\" NumberOfVerts=\"" << total_generated_particles << "\">\n";

        // write current/final particle positions first
        out_file << "   <Points>\n";
        out_file << "    <DataArray Name=\"Position\" type=\"Float32\"  NumberOfComponents=\"3\" format=\"ascii\">\n";
        out_file << "    ";
        for (size_t i = 0; i != total_generated_particles; ++i)
        {
            Vec3d particle_position = upgradeToVec3d(position_[i]);
            out_file << particle_position[0] << " " << particle_position[1] << " " << particle_position[2] << " ";
        }
        out_file << std::endl;
        out_file << "    </DataArray>\n";
        out_file << "   </Points>\n";

        // write empty cells
        out_file << "   <Verts>\n";
        out_file << "    <DataArray type=\"Int32\"  Name=\"connectivity\"  format=\"ascii\">\n";
        out_file << "    ";
        for (size_t i = 0; i != total_generated_particles; ++i)
        {
            out_file << i << " ";
        }
        out_file << std::endl;
        out_file << "    </DataArray>\n";
        out_file << "    <DataArray type=\"Int32\"  Name=\"offsets\"  format=\"ascii\">\n";
        out_file << "    ";
        for (size_t i = 0; i != total_generated_particles; ++i)
        {
            out_file << i + 1 << " ";
        }
        out_file << std::endl;
        out_file << "    </DataArray>\n";
        out_file << "   </Verts>\n";

        out_file << "  </Piece>\n";
        out_file << " </PolyData>\n";
        out_file << "</VTKFile>\n";

        out_file.close();
    }
}
//=================================================================================================//
} // namespace SPH
