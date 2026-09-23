# Windows Installation (VS 2022)

## Prerequisites

Install:

- Visual Studio 2022 (Desktop development with C++)
- CMake
- Ninja
- Git
- Python 3

Use x64 Native Tools Command Prompt for VS 2022 or Developer PowerShell.

## Clone Repository

```powershell
git clone --recurse-submodules https://github.com/<your-org>/SPHinXsim.git
cd SPHinXsim
```

## Install vcpkg

```powershell
git clone https://github.com/microsoft/vcpkg.git ..\vcpkg
..\vcpkg\bootstrap-vcpkg.bat
```

## Install C/C++ Dependencies

```powershell
..\vcpkg\vcpkg.exe install --clean-after-build openblas[dynamic-arch] --allow-unsupported
..\vcpkg\vcpkg.exe install --clean-after-build `
  eigen3 `
  tbb `
  boost-program-options `
  boost-geometry `
  simbody `
  spdlog `
  gtest `
  pybind11 `
  nlohmann-json
```

## Configure and Build

For a pip-first workflow, install directly from source (this builds C++ extensions via CMake):

```powershell
$env:CMAKE_ARGS='-D CMAKE_TOOLCHAIN_FILE="' + "$pwd\..\vcpkg\scripts\buildsystems\vcpkg.cmake" + '" -D CMAKE_C_COMPILER=cl -D CMAKE_CXX_COMPILER=cl'
python -m pip install -e ".[dev,visualization]"
```

Optional manual CMake workflow:

```powershell
cmake --preset integrated-build-release-windows `
  -D CMAKE_TOOLCHAIN_FILE="$pwd\..\vcpkg\scripts\buildsystems\vcpkg.cmake" `
  -D Python3_EXECUTABLE="$(python -c "import sys; print(sys.executable)")"
cmake --build --preset integrated-build-release-windows --parallel
```


To avoid issue of insufficient memory,
you need to pick the number of ` "$(nproc)"` based on your RAM.
Rough rule: ~1.5–2 GB RAM per job for C++ projects. For example you can build with

```bash
cmake --build --preset integrated-build-release --parallel 4
```

or using percentage 

```bash
cmake --build --preset integrated-build-release --parallel 50%
```

Install compiled C++ extension modules into the active Python environment (manual workflow only):

```powershell
cmake --install build-integrated --prefix "$(python -c "import sys; print(sys.prefix)")"
```

## Run Tests

Install Python package with dev dependencies:

```powershell
python -m pip install -e ".[dev]"
```

To enable geometry preview (`sphinxsim preview`), also install:

```powershell
python -m pip install -e ".[visualization]"
```

Run Python tests:

```powershell
python -m pytest tests/ examples/ -v
```

Verify extension import explicitly:

```powershell
python -c "from sphinxsim.bindings.loader import load_sphinxsys_core; print(load_sphinxsys_core().__name__)"
```

Run C++ simulation tests:

```powershell
ctest --test-dir build-integrated --output-on-failure -R "test_(2d|3d)_simulation$"
```

## Notes

- If `cl` is not found, reopen a Visual Studio developer shell and rerun configure.
- If CMake cache gets inconsistent, remove `build-integrated` and reconfigure.
