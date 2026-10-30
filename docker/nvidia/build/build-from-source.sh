#!/usr/bin/env bash
# Portable entry point: downloaded source + this bundle -> a local Docker image.
set -euo pipefail
if [[ $# != 3 ]]; then
    echo "Usage: $0 {3090-cuda12|h20-cuda12|3090-cuda13|h20-cuda13} SOURCE_DIRECTORY NEW_CONTEXT_DIRECTORY" >&2
    exit 2
fi
target=$1
case "$target" in 3090-cuda12|h20-cuda12|3090-cuda13|h20-cuda13) ;; *)
    echo "Unknown target: $target" >&2; exit 2 ;;
esac
script_dir=$(cd "$(dirname "$0")" && pwd)
# All preparation is local except the explicit dependency-download step below.
prepare=(python3 "$script_dir/prepare-context.py" "$3" --source "$2")
[[ -z ${SOURCE_REVISION:-} ]] || prepare+=(--revision "$SOURCE_REVISION")
"${prepare[@]}"
context=$(cd "$3" && pwd)
dependency_args=()
[[ ${DOWNLOAD_DEPENDENCIES:-1} == 0 ]] || dependency_args+=(--download)
[[ -z ${PACKAGE_CACHE:-} ]] || dependency_args+=(--cache "$PACKAGE_CACHE")
[[ -z ${DOWNLOAD_PROXY:-} ]] || dependency_args+=(--proxy "$DOWNLOAD_PROXY")
[[ ${ALLOW_LARGE_PROXY:-0} != 1 ]] || dependency_args+=(--allow-large-proxy)
python3 "$context/build/dependencies.py" "${target#*-}" "${dependency_args[@]}"
bash "$context/build/build.sh" "$target" "$context"
