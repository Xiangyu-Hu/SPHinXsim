# Linux Installation (Ubuntu 22.04)

## Prerequisites

Install system packages:

```bash
sudo apt-get update
sudo apt-get install -y \
  apt-utils \
  build-essential \
  curl zip unzip tar \
  pkg-config git \
  ninja-build \
  autoconf automake autoconf-archive \
  libxmu-dev libxi-dev libgl-dev libxt-dev \
  libxcb-cursor0
```

## Clone Repository

```bash
git clone --recurse-submodules https://github.com/<your-org>/SPHinXsim.git
cd SPHinXsim
```
## Install vcpkg

```bash
git clone https://github.com/microsoft/vcpkg.git ../vcpkg
../vcpkg/bootstrap-vcpkg.sh
```

## Install C/C++ Dependencies

```bash
../vcpkg/vcpkg install --clean-after-build openblas[dynamic-arch] --allow-unsupported
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

## Configure and Build

For a pip-first workflow, install directly from source (this builds C++ extensions via CMake):

```bash
CMAKE_ARGS="-D CMAKE_TOOLCHAIN_FILE=$(pwd)/../vcpkg/scripts/buildsystems/vcpkg.cmake" \
python3 -m pip install -e ".[dev,visualization]"
```

Optional manual CMake workflow:

```bash
cmake --preset integrated-build-release \
  -D CMAKE_TOOLCHAIN_FILE="$(pwd)/../vcpkg/scripts/buildsystems/vcpkg.cmake" \
  -D Python3_EXECUTABLE="$(which python3)"
cmake --build --preset integrated-build-release --parallel "$(nproc)"
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

Install compiled C++ extension modules into the active Python environment:

```bash
cmake --install build-integrated --prefix "$(python3 -c 'import sys; print(sys.prefix)')"
```

## Run Tests

Install Python package with dev dependencies:

```bash
python -m pip install -e ".[dev]"
```

To enable geometry preview (`sphinxsim preview`), also install:

```bash
python -m pip install -e ".[visualization]"
```

Run Python tests:

```bash
python -m pytest tests/ examples/ -v
```

Run C++ simulation tests:

```bash
ctest --test-dir build-integrated --output-on-failure -R "test_(2d|3d)_simulation$"
```
