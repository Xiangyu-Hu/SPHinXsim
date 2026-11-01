#!/usr/bin/env bash
set -eo pipefail
source /opt/intel/oneapi/setvars.sh --force >/dev/null
if [[ "${1:-}" == "--exec" ]]; then
    shift
    exec "$@"
fi
exec python /opt/sphinxsim-container/run_case.py "$@"
