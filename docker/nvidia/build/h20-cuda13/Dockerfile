# Context: source/, downloads/, build/. See ../README.md.
FROM nvidia/cuda:13.0.3-devel-ubuntu22.04@sha256:3869b846a8cc495ce11c172d87cfc0da8874b910d14a9810bec6b6182e9ee9f8 AS sdk
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive
RUN sed -i -E 's@https?://(archive|security).ubuntu.com/ubuntu/?@https://mirrors.ustc.edu.cn/ubuntu/@g' /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/cuda*.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
       | gpg --dearmor -o /usr/share/keyrings/oneapi-archive-keyring.gpg \
    && echo 'deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main' > /etc/apt/sources.list.d/oneapi.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       intel-oneapi-compiler-dpcpp-cpp-2026.1=2026.1.1-325 \
       build-essential gfortran git cmake ninja-build pkg-config \
       python3 python3-dev python3-venv python3-pip \
       libeigen3-dev libtbb-dev libboost-dev libboost-program-options-dev \
       libsimbody-dev libspdlog-dev nlohmann-json3-dev pybind11-dev \
       libopenblas-dev libgtest-dev libnuma1 libxml2 libatomic1 time \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -i https://mirrors.ustc.edu.cn/pypi/simple \
       pip==25.1.1 scikit-build-core==0.11.6 pybind11==2.13.6 \
       numpy==2.2.6 pydantic==2.11.7 pytest==8.4.1
COPY downloads/sycl-v7.1.0-linux.tar.gz /tmp/sycl.tar.gz
RUN echo '992de12d5c68ba8fd630446b1a97487361b38807b33140769e4c818795f0fcab  /tmp/sycl.tar.gz' | sha256sum -c - \
    && mkdir -p /opt/ur-cuda \
    && tar -xzf /tmp/sycl.tar.gz -C /opt/ur-cuda --strip-components=2 --wildcards './lib/libur_adapter_cuda.so*' \
    && rm /tmp/sycl.tar.gz
COPY downloads/boost_1_85_0.tar.bz2 /tmp/boost.tar.bz2
RUN echo '7009fe1faa1697476bdc7027703a2badb84e849b7b0baad5086b087b971f8617  /tmp/boost.tar.bz2' | sha256sum -c - \
    && tar -xjf /tmp/boost.tar.bz2 -C /tmp \
    && cd /tmp/boost_1_85_0 \
    && ./bootstrap.sh --prefix=/opt/boost --with-libraries=program_options \
    && ./b2 -j4 variant=release link=static threading=multi cxxflags=-fPIC install \
    && rm -rf /tmp/boost.tar.bz2 /tmp/boost_1_85_0
ENV PATH=/opt/venv/bin:${PATH}

FROM sdk AS build
ARG BUILD_JOBS=8
COPY source/ /opt/SPHinXsim/
COPY build/runtime/sycl_probe.cpp /tmp/sycl_probe.cpp
RUN source /opt/intel/oneapi/setvars.sh >/dev/null \
    && export LD_LIBRARY_PATH=/opt/ur-cuda:${LD_LIBRARY_PATH:-} \
    && cmake -S /opt/SPHinXsim -B /tmp/sph-build -G Ninja \
       -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx \
       -DPython3_EXECUTABLE=/opt/venv/bin/python \
       -DBoost_ROOT=/opt/boost -DBOOST_ROOT=/opt/boost -DBoost_NO_SYSTEM_PATHS=ON \
       -DSPHINXSYS_USE_SYCL=ON -DSPHINXSYS_SYCL_TARGETS=nvptx64-nvidia-cuda \
       -DSPHINXSYS_USE_FLOAT=ON -DSPHINXSYS_DEVELOPER_MODE=OFF \
       -DSPHINXSYS_BUILD_TESTS=OFF -DSPHINXSIM_BUILD_SIMULATION_TESTS=OFF \
       -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
       -DCMAKE_CXX_FLAGS="--cuda-path=/usr/local/cuda -Xsycl-target-backend=nvptx64-nvidia-cuda --cuda-gpu-arch=sm_90" \
    && cmake --build /tmp/sph-build --parallel ${BUILD_JOBS} \
    && cmake --install /tmp/sph-build --prefix /opt/SPHinXsim \
    && icpx -O3 -fsycl -fsycl-targets=nvptx64-nvidia-cuda --cuda-path=/usr/local/cuda \
       -Xsycl-target-backend=nvptx64-nvidia-cuda --cuda-gpu-arch=sm_90 \
       /tmp/sycl_probe.cpp -o /usr/local/bin/sphinxsim-gpu-check \
    && mkdir -p /opt/SPHinXsim/build-info \
    && cp /tmp/sph-build/CMakeCache.txt /tmp/sph-build/compile_commands.json /opt/SPHinXsim/build-info/ \
    && icpx --version > /opt/SPHinXsim/build-info/compiler.txt \
    && nvcc --version > /opt/SPHinXsim/build-info/cuda.txt \
    && dpkg-query -W > /opt/SPHinXsim/build-info/apt-packages.txt \
    && python -m pip freeze > /opt/SPHinXsim/build-info/python-packages.txt \
    && sha256sum /opt/ur-cuda/libur_adapter_cuda.so.0 > /opt/SPHinXsim/build-info/adapter-sha256.txt

FROM sdk AS runtime
ARG SOURCE_REVISION
LABEL org.opencontainers.image.title="SPHinXsim CUDA 13 / oneAPI 2026.1.1" \
      org.opencontainers.image.revision=${SOURCE_REVISION} \
      org.opencontainers.image.source="https://github.com/Xiangyu-Hu/SPHinXsim" \
      sphinxsim.cuda.arch=sm_90 \
      sphinxsim.cuda.toolkit="13.0.3" \
      sphinxsim.oneapi.compiler="2026.1.1" \
      sphinxsim.ur.cuda.source="intel/llvm v7.1.0"
COPY --from=build /opt/SPHinXsim /opt/SPHinXsim
COPY source-manifest.json /opt/SPHinXsim/build-info/source-manifest.json
COPY --from=build /usr/local/bin/sphinxsim-gpu-check /usr/local/bin/sphinxsim-gpu-check
COPY build/runtime/entrypoint-cuda13.sh /usr/local/bin/sphinxsim-entrypoint
COPY build/runtime/run_case.py /opt/sphinxsim-container/run_case.py
COPY build/runtime/sycl_probe.cpp /opt/sphinxsim-container/sycl_probe.cpp
RUN chmod 755 /usr/local/bin/sphinxsim-entrypoint \
    && mkdir -p /work /tmp/sphinxsim-cache \
    && chmod 1777 /work /tmp/sphinxsim-cache
ENV PYTHONPATH=/opt/SPHinXsim \
    PYTHONDONTWRITEBYTECODE=1 \
    ONEAPI_DEVICE_SELECTOR=cuda:gpu \
    SYCL_CACHE_PERSISTENT=1 \
    SYCL_CACHE_DIR=/tmp/sphinxsim-cache
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/sphinxsim-entrypoint"]
CMD ["--help"]
