#!/usr/bin/env bash
set -eo pipefail
source /opt/intel/oneapi/setvars.sh --force >/dev/null
export LD_LIBRARY_PATH=/opt/ur-cuda:${LD_LIBRARY_PATH:-}
if [[ "${1:-}" == "--exec" ]]; then
    shift
    exec "$@"
fi
exec python /opt/sphinxsim-container/run_case.py "$@"
