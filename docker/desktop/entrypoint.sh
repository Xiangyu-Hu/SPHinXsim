#!/bin/bash
set -euo pipefail
umask 077

if [[ $(id -u) == 0 ]]; then
    echo 'The desktop must run as the unprivileged simulation user.' >&2
    exit 1
fi
password_file=/run/secrets/desktop_password
if [[ ! -r $password_file ]]; then
    echo 'Mount a desktop password at /run/secrets/desktop_password (at least 16 characters).' >&2
    exit 1
fi
password=$(cat "$password_file")
if [[ ${#password} -lt 16 || ${#password} -gt 128 || $password == *$'\n'* || $password == *$'\r'* ]]; then
    echo 'The desktop password must be one line of 16–128 characters.' >&2
    exit 1
fi
mkdir -p "$HOME/.vnc" "$HOME/.config/autostart" /work/project
cp /opt/sphinxsim-desktop/xstartup "$HOME/.vnc/xstartup"
cp /opt/sphinxsim-desktop/kasmvnc.yaml "$HOME/.vnc/kasmvnc.yaml"
cp /opt/sphinxsim-desktop/SPHinXsim.desktop "$HOME/.config/autostart/SPHinXsim.desktop"
# Docker stop can leave Xvnc's files behind even though all container processes
# are gone. This image owns display :1; never remove locks from a live server.
if pgrep -x Xvnc >/dev/null; then
    echo 'An Xvnc server is already active in this desktop container.' >&2
    exit 1
fi
rm -f -- /tmp/.X1-lock /tmp/.X11-unix/X1 "$HOME"/.vnc/*:1.pid
printf '%s\n%s\n' "$password" "$password" | vncpasswd -u simulation -r -w > /dev/null
unset password
if [[ ! -s $HOME/.vnc/tls.key || ! -s $HOME/.vnc/tls.crt ]]; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
        -subj '/CN=sphinxsim-desktop' \
        -addext 'subjectAltName=DNS:localhost,DNS:sphinxsim-desktop,IP:127.0.0.1' \
        -keyout "$HOME/.vnc/tls.key" -out "$HOME/.vnc/tls.crt" 2>/dev/null
fi
export XDG_RUNTIME_DIR=/tmp/sphinxsim-desktop-runtime
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# The native CLI locates its project root by pyproject.toml. Give each desktop
# its own root so .build-temp never writes into the shared installed source.
if [[ ! -e /work/project/pyproject.toml ]]; then
    cp /opt/SPHinXsim/pyproject.toml /work/project/pyproject.toml
fi
case "${SPHINXSIM_DESKTOP_RENDERER}" in
    nvidia) ;;
    # softpipe avoids llvmpipe LLVM JIT failures under ARM-hosted amd64 emulation.
    software) export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=softpipe ;;
    *) echo 'SPHINXSIM_DESKTOP_RENDERER must be nvidia or software (diagnostics only).' >&2; exit 1 ;;
esac
bind_interface=0.0.0.0
if [[ ${SPHINXSIM_DESKTOP_MANAGED:-0} == 1 ]]; then bind_interface=127.0.0.1; fi
echo "Starting SPHinXsim desktop (renderer=${SPHINXSIM_DESKTOP_RENDERER}); project=/work/project"
# Foreground mode lets the container runtime observe server/session failure.
# HOME is a noexec tmpfs in managed containers. Execute the immutable launcher
# from the image instead of a copied script under that mount.
exec vncserver :1 -fg -xstartup /opt/sphinxsim-desktop/xstartup -geometry 1600x1000 -depth 24 -websocketPort 6901 -interface "$bind_interface"
