# Build context: docker/desktop (see build.sh).
ARG SOLVER_IMAGE
FROM ${SOLVER_IMAGE}
USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive
ARG APT_MIRROR=http://archive.ubuntu.com/ubuntu
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="SPHinXsim native agent desktop" \
      org.opencontainers.image.revision=${SOURCE_REVISION} \
      org.sphinxsim.desktop.stage="managed" \
      org.sphinxsim.desktop.kasmvnc="1.5.0" org.sphinxsim.desktop.virtualgl="3.1.5"

# Do not update the existing oneAPI/CUDA installation while adding desktop dependencies.
RUN sed -i -E "s@https?://[^ /]+/ubuntu/?@${APT_MIRROR}/@g" /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/oneapi.list /etc/apt/sources.list.d/cuda*.list
COPY .build/*.deb /tmp/desktop-packages/
RUN echo '485c190b1d9269fc40fc7205f850ecf71292d737cdb5482e778f2e52f128cd8a  /tmp/desktop-packages/kasmvncserver_jammy_1.5.0_amd64.deb' | sha256sum -c - \
    && echo 'df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e  /tmp/desktop-packages/virtualgl_3.1.5_amd64.deb' | sha256sum -c - \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends \
       xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xterm \
       python3-venv dbus-x11 at-spi2-core xdotool mesa-utils libgl1-mesa-dri libegl1 libopengl0 \
       libglu1-mesa libxkbcommon-x11-0 libxcb-cursor0 libxcb-xinerama0 \
       libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxcb-shape0 \
       libnss3 libasound2 libxtst6 fonts-noto-cjk tini openssl procps \
       /tmp/desktop-packages/*.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/desktop-packages
COPY requirements.txt /opt/sphinxsim-desktop/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --timeout 45 --index-url "${PIP_INDEX_URL}" \
       -r /opt/sphinxsim-desktop/requirements.txt \
    && /opt/venv/bin/pip check \
    && /opt/venv/bin/pip freeze > /opt/sphinxsim-desktop/python-packages.txt \
    && dpkg-query -W > /opt/sphinxsim-desktop/apt-packages.txt
# Reuse the isolated OCCT/CadQuery environment from the agent image.
RUN /opt/sphinxsim-cad/bin/pip check \
    && /opt/sphinxsim-cad/bin/pip freeze > /opt/sphinxsim-desktop/cad-packages.txt
RUN ! ldd /opt/venv/lib/python3.10/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so | grep 'not found'
COPY session.sh xstartup kasmvnc.yaml SPHinXsim.desktop /opt/sphinxsim-desktop/
RUN chmod 755 /opt/sphinxsim-desktop/session.sh /opt/sphinxsim-desktop/xstartup
COPY embed.js /usr/share/kasmvnc/www/assets/sphinxsim-embed.js
COPY embed.css /usr/share/kasmvnc/www/assets/sphinxsim-embed.css
COPY embed-policy.js /usr/share/kasmvnc/www/assets/sphinxsim-embed-policy.js
RUN /opt/venv/bin/python -m sphinxsim_agent.desktop.patch_embed
COPY entrypoint.sh /usr/local/bin/sphinxsim-desktop
COPY xstartup /home/simulation/.vnc/xstartup
COPY kasmvnc.yaml /home/simulation/.vnc/kasmvnc.yaml
COPY SPHinXsim.desktop /home/simulation/.config/autostart/SPHinXsim.desktop
RUN printf '#!/bin/bash\nexec /usr/local/bin/sphinxsim-entrypoint --exec /opt/venv/bin/python -m sphinxsim_agent.desktop.native_cli "$@"\n' > /usr/local/bin/sphinxsim-native \
    && chmod 755 /usr/local/bin/sphinxsim-desktop /usr/local/bin/sphinxsim-native /home/simulation/.vnc/xstartup \
    && mkdir -p /work/project \
    && chown -R 10001:10001 /home/simulation/.vnc /home/simulation/.config /work/project
ENV HOME=/home/simulation \
    SPHINXSIM_CAD_PYTHON=/opt/sphinxsim-cad/bin/python \
    SPHINXSIM_ADAPTER_REVISION=${SOURCE_REVISION} \
    DISPLAY=:1 \
    QT_API=pyside6 \
    QT_QPA_PLATFORM=xcb \
    LANG=C.UTF-8 \
    NVIDIA_VISIBLE_DEVICES=void \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics \
    VGL_DISPLAY=egl \
    SPHINXSIM_DESKTOP_RENDERER=nvidia
WORKDIR /opt/SPHinXsim
RUN /opt/venv/bin/python -c 'import inspect; from sphinxsim.cli import _ShellPreviewRuntime; s=inspect.getsource(_ShellPreviewRuntime._install_json_editor); assert "is_dirty" in s and "discard" in s, "Native package needs the documented editor extension hooks"'
USER 10001:10001
WORKDIR /work/project
EXPOSE 6901
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/sphinxsim-desktop"]
