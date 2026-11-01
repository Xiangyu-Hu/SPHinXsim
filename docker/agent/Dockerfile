# Build context: repository root, with -f docker/agent/Dockerfile.
# Supply a native SPHinXsim image explicitly.
ARG SOLVER_IMAGE
FROM ${SOLVER_IMAGE}
USER root
ARG DEBIAN_FRONTEND=noninteractive
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="SPHinXsim agent integration" \
      org.opencontainers.image.revision=${SOURCE_REVISION} \
      org.sphinxsim.agent.protocol="1"
RUN rm -f /etc/apt/sources.list.d/oneapi.list /etc/apt/sources.list.d/cuda*.list \
    && apt-get update && apt-get install -y --no-install-recommends \
       libgl1 libglib2.0-0 libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*
COPY integrations/agent/ /opt/sphinxsim-agent
RUN /opt/venv/bin/pip install --no-cache-dir --index-url ${PIP_INDEX_URL} '/opt/sphinxsim-agent[mcp,export]' \
    && /opt/venv/bin/pip check \
    && python3 -m venv /opt/sphinxsim-cad \
    && /opt/sphinxsim-cad/bin/pip install --no-cache-dir --index-url ${PIP_INDEX_URL} '/opt/sphinxsim-agent[cad]' \
    && /opt/sphinxsim-cad/bin/pip check \
    && (id -u simulation >/dev/null 2>&1 || useradd --uid 10001 --create-home simulation) \
    && mkdir -p /work && chown simulation:simulation /work
ENV SPHINXSIM_CAD_PYTHON=/opt/sphinxsim-cad/bin/python \
    PYTHONUNBUFFERED=1 SPHINXSIM_ADAPTER_REVISION=${SOURCE_REVISION}
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/sphinxsim-entrypoint", "--exec", "/opt/venv/bin/python", "-m", "sphinxsim_agent.runner"]
CMD ["/work/request.json"]
