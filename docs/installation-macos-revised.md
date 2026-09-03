# macOS Installation

This guide assumes an Apple Silicon Mac and a checkout layout in which `vcpkg`
is a sibling of the `SPHinXsim` repository:

```text
Sphinx/
├── SPHinXsim/
└── vcpkg/
```

## 1. Install system tools

Install the build tools, CMake, and the Python version that matches vcpkg's
current Python development package.

```bash
brew update
brew install \
  cmake ninja ccache \
  autoconf automake autoconf-archive libtool pkg-config \
  python@3.12
```

Do not replace macOS's `/usr/bin/python3`. It is system-managed. Homebrew's
unversioned `python3` may point to a newer version (for example Python 3.14),
so use `python@3.12` explicitly when creating this project's environment.

## 2. Clone the repository

```bash
git clone --recurse-submodules https://github.com/<your-org>/SPHinXsim.git
cd SPHinXsim
```

## 3. Create and activate a Python environment

```bash
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
source .venv/bin/activate
python --version
python -m pip install --upgrade pip
```

`python --version` should report Python 3.12. Activate `.venv` in every new
terminal before working on the project:

```bash
cd /path/to/SPHinXsim
source .venv/bin/activate
```

## 4. Install vcpkg

```bash
git clone https://github.com/microsoft/vcpkg.git ../vcpkg
../vcpkg/bootstrap-vcpkg.sh
```

## 5. Install C/C++ dependencies

In zsh, quote the package feature name: square brackets otherwise have a
special filename-matching meaning.

```bash
../vcpkg/vcpkg install --clean-after-build \
  'openblas[dynamic-arch]' --allow-unsupported

../vcpkg/vcpkg install --clean-after-build \
  eigen3 \
  tbb \
  boost-program-options \
  boost-geometry \
  simbody \
  spdlog \
  gtest \
  pybind11 \
  nlohmann-json
```

If the installation stopped previously, rerun the same command after fixing
the missing prerequisite. vcpkg will keep packages that have already been
installed and continue with the remaining ones.

`dynamic-arch` is an OpenBLAS feature that builds optimized code for multiple
CPU architectures and selects the appropriate implementation at runtime. It
does not mean dynamic linking.

## 6. Recommended: install the Python package from source

CMake must be told where vcpkg installed C++ dependencies such as
`nlohmann_json`. Set the toolchain argument once in the active terminal:

```bash
export CMAKE_ARGS="-DCMAKE_TOOLCHAIN_FILE=$(pwd)/../vcpkg/scripts/buildsystems/vcpkg.cmake"
```

Then install the project and its development and visualization dependencies:

```bash
python -m pip install -e ".[dev,visualization]"
```

`-e` means editable installation: changes to the Python source are used
directly without reinstalling the package. `CMAKE_ARGS` lasts only for the
current terminal; set it again after opening a new terminal, or supply it
directly before the pip command.

## 7. Optional: configure and build with CMake directly

The `integrated-build-release` preset already specifies the vcpkg toolchain
and writes build files to `build-integrated`. Pass the active virtual
environment's Python explicitly:

```bash
cmake --preset integrated-build-release \
  -DPython3_EXECUTABLE="$(which python)"
cmake --build --preset integrated-build-release \
  --parallel "$(sysctl -n hw.logicalcpu)"
```

To install the compiled extension into the active virtual environment:

```bash
cmake --install build-integrated \
  --prefix "$(python -c 'import sys; print(sys.prefix)')"
```

## 8. Run tests

Run Python tests:

```bash
python -m pytest tests/ examples/ -v
```

Run the registered 2D and 3D C++ simulation tests:

```bash
ctest --test-dir build-integrated --output-on-failure \
  -R "test_(2d|3d)_simulation"
```

Do not add a trailing `$` to this expression. The registered test names have
additional suffixes, for example
`test_2d_simulation.json/Json.run/column_collapse`; a trailing `$` would match
no tests.

To see all registered C++ tests without running them:

```bash
ctest --test-dir build-integrated -N
```

## Optional: use CMake Tools in VS Code

Install the **CMake Tools** extension, then use the Command Palette
(`Cmd+Shift+P`) to select **CMake: Select Configure Preset** and choose
**Integrated SPHinXsys + Python Release Build**. Run **CMake: Configure** and
then **CMake: Build**. Building compiles the programs; run **CMake: Run Tests**
or use the Testing view to execute simulations.

If CMake Tools reports `Bad CMake executable: /usr/bin/cmake`, set this user
setting and reload the VS Code window:

```json
{
  "cmake.cmakePath": "/opt/homebrew/bin/cmake"
}
```
