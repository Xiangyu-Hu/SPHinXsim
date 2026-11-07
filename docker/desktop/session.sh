#!/bin/bash
set -euo pipefail
if [[ ${SPHINXSIM_DESKTOP_MANAGED:-0} == 1 ]]; then
    mkdir -p /work/control
    # HOME is ephemeral. Keep startup/runtime failures in the owned workdir so
    # a terminated X session does not erase the cause before Worker observes it.
    exec >> /work/control/native-desktop.log 2>&1
    export CUA_DRIVER_ENVELOPE_HTTP_PORT=6903
    export CUA_DRIVER_ENVELOPE_PERMISSION_MODE=unrestricted
    export DO_NOT_TRACK=1 CUA_TELEMETRY=0
    xfwm4 --replace > /work/control/window-manager.log 2>&1 &
    if command -v cua-driver >/dev/null; then
        cua-driver --dangerously-bypass-approvals serve > /work/control/cua-driver.log 2>&1 &
    fi
    exec /usr/local/bin/sphinxsim-native
fi
exec xfce4-session
