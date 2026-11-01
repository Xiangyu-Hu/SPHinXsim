#!/usr/bin/env bash
# Explicit opt-in to building all four targets, sequentially to bound resources.
set -euo pipefail
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ $# -le 1 ]] || { echo "Usage: $0 [context]" >&2; exit 2; }
if [[ -n ${IMAGE_TAG:-} ]]; then
    echo 'Unset IMAGE_TAG for the matrix; set IMAGE_PREFIX to change all tags.' >&2
    exit 2
fi
for target in 3090-cuda12 h20-cuda12 3090-cuda13 h20-cuda13; do
    bash "$script_dir/build.sh" "$target" "${1:-$script_dir/..}"
done
