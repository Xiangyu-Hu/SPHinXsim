# Windows Installation (VS 2022)

## Prerequisites

Install:

- Visual Studio 2022 (Desktop development with C++)
- CMake
- Ninja
- Git
- Python 3

Use x64 Native Tools Command Prompt for VS 2022 or Developer PowerShell.
Windows CI uses Python 3.12. A fresh virtual environment avoids conflicts with
packages already installed in your Python environment.

## Clone Repository

```powershell
git clone --recurse-submodules https://github.com/<your-org>/SPHinXsim.git
cd SPHinXsim
```

Create and activate a new Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
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
python -m pip install -v -e ".[dev]"
python -m pip install pyvistaqt PySide6
```

This matches the Windows CI Python dependency selection. It does not install
the optional `cad` extra: CAD modeling requires CadQuery and is not available
without it. CadQuery and its CasADi dependency can load runtime DLLs
that conflict with the SPHinXsim native bindings on Windows. If CadQuery is
already installed, omitting the extra does not remove it; use a clean
environment or, if you no longer need CadQuery there, run
`python -m pip uninstall cadquery`.

You may face the long-path issue in Windows System. If it is the case, you can follow the direction: 
https://learn.microsoft.com/en-us/answers/questions/6011933/how-to-fix-long-path-long-file-names-in-windows-11.

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

To match Windows CI and enable geometry preview (`sphinxsim preview`), also install:

```powershell
python -m pip install pyvistaqt PySide6
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
