FROM nvidia/cuda:12.3.1-devel-ubuntu22.04@sha256:279f5feac6b1d0ec7219f8fb091ea9721882ba772308a180023e1ef17402bdd2 AS system
ARG DEBIAN_FRONTEND=noninteractive
RUN sed -i -E 's@https?://(archive|security).ubuntu.com/ubuntu/?@https://mirrors.ustc.edu.cn/ubuntu/@g' /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/cuda*.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git build-essential gfortran cmake ninja-build pkg-config \
       python3 python3-dev python3-venv python3-pip \
       libeigen3-dev libtbb-dev libboost-dev libboost-program-options-dev \
       libsimbody-dev libspdlog-dev nlohmann-json3-dev pybind11-dev \
       libopenblas-dev libgtest-dev libnuma1 libxml2 libatomic1 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -i https://mirrors.ustc.edu.cn/pypi/simple \
       pip==25.1.1 scikit-build-core==0.11.6 pybind11==2.13.6 \
       numpy==2.2.6 pydantic==2.11.7 pytest==8.4.1
ENV PATH=/opt/venv/bin:${PATH}

FROM system AS toolchain
COPY downloads/intel-oneapi-base-toolkit-2025.2.0.592_offline.sh /tmp/oneapi.sh
COPY downloads/oneapi-for-nvidia-gpus-2025.2.0-linux.sh /tmp/codeplay.sh
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN echo '5cf60b1538edb74dbf8a1c6f0f3886982639dab709bb75763f17a5acefbac530  /tmp/oneapi.sh' | sha256sum -c - \
    && echo '84c0225131da6d066144a56ab3bd6213314a894ef4346d400140185b2f97bdc7  /tmp/codeplay.sh' | sha256sum -c - \
    && bash /tmp/oneapi.sh -a --silent --eula accept \
       --components intel.oneapi.lin.dpcpp-cpp-compiler:intel.oneapi.lin.dpl:intel.oneapi.lin.tbb.devel \
    && bash /tmp/codeplay.sh --yes --install-dir /opt/intel/oneapi \
    && rm -f /tmp/oneapi.sh /tmp/codeplay.sh

FROM system AS sdk
COPY --from=toolchain /opt/intel /opt/intel
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

FROM sdk AS build
ARG BUILD_JOBS=2
COPY downloads/boost_1_85_0.tar.bz2 /tmp/boost.tar.bz2
RUN echo '7009fe1faa1697476bdc7027703a2badb84e849b7b0baad5086b087b971f8617  /tmp/boost.tar.bz2' | sha256sum -c - \
    && tar -xjf /tmp/boost.tar.bz2 -C /tmp \
    && cd /tmp/boost_1_85_0 \
    && ./bootstrap.sh --prefix=/opt/boost --with-libraries=program_options \
    && ./b2 -j4 variant=release link=static threading=multi cxxflags=-fPIC install \
    && rm -rf /tmp/boost.tar.bz2 /tmp/boost_1_85_0
COPY source/ /opt/SPHinXsim/
WORKDIR /opt/SPHinXsim
RUN source /opt/intel/oneapi/setvars.sh >/dev/null \
    && cmake -S . -B /tmp/sph-build -G Ninja \
       -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx \
       -DPython3_EXECUTABLE=/opt/venv/bin/python \
       -DBoost_ROOT=/opt/boost -DBOOST_ROOT=/opt/boost -DBoost_NO_SYSTEM_PATHS=ON \
       -DSPHINXSYS_USE_SYCL=ON -DSPHINXSYS_SYCL_TARGETS=nvptx64-nvidia-cuda \
       -DSPHINXSYS_USE_FLOAT=ON -DSPHINXSYS_DEVELOPER_MODE=OFF \
       -DSPHINXSYS_BUILD_TESTS=OFF -DSPHINXSIM_BUILD_SIMULATION_TESTS=OFF \
       -DCMAKE_CXX_FLAGS="-Xsycl-target-backend=nvptx64-nvidia-cuda --cuda-gpu-arch=sm_90" \
    && cmake --build /tmp/sph-build --parallel ${BUILD_JOBS} \
    && cmake --install /tmp/sph-build --prefix /opt/SPHinXsim \
    && mkdir -p /opt/SPHinXsim/build-info \
    && cp /tmp/sph-build/CMakeCache.txt /opt/SPHinXsim/build-info/ \
    && dpkg-query -W > /opt/SPHinXsim/build-info/apt-packages.txt \
    && python -m pip freeze > /opt/SPHinXsim/build-info/python-packages.txt

FROM sdk AS runtime
ARG SOURCE_REVISION
LABEL org.opencontainers.image.title="SPHinXsim NVIDIA SYCL" \
      org.opencontainers.image.revision=${SOURCE_REVISION} \
      org.opencontainers.image.source="https://github.com/Xiangyu-Hu/SPHinXsim" \
      sphinxsim.cuda.arch=sm_90
COPY --from=build /opt/SPHinXsim /opt/SPHinXsim
COPY source-manifest.json /opt/SPHinXsim/build-info/source-manifest.json
COPY build/runtime/entrypoint-cuda12.sh /usr/local/bin/sphinxsim-entrypoint
COPY build/runtime/run_case.py /opt/sphinxsim-container/run_case.py
COPY build/runtime/sycl_probe.cpp /opt/sphinxsim-container/sycl_probe.cpp
RUN chmod 755 /usr/local/bin/sphinxsim-entrypoint \
    && source /opt/intel/oneapi/setvars.sh >/dev/null \
    && icpx -fsycl -fsycl-targets=nvptx64-nvidia-cuda \
       -Xsycl-target-backend=nvptx64-nvidia-cuda --cuda-gpu-arch=sm_90 \
       /opt/sphinxsim-container/sycl_probe.cpp -o /usr/local/bin/sphinxsim-gpu-check \
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
