#!/usr/bin/env bash
# One target = one independent Dockerfile; never downloads archive dependencies.
set -euo pipefail
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 {3090-cuda12|h20-cuda12|3090-cuda13|h20-cuda13} [context]" >&2
    exit 2
fi
target=$1
case "$target" in
    3090-cuda12) image_suffix=3090 ;;
    h20-cuda12) image_suffix=h20 ;;
    3090-cuda13|h20-cuda13) image_suffix=$target ;;
    *) echo "Unknown target: $target" >&2; exit 2 ;;
esac
context=$(cd "${2:-$script_dir/..}" && pwd)
recipe="$context/build/$target/Dockerfile"
[[ -f "$recipe" && -f "$context/.dockerignore" && -s "$context/source-revision.txt" && -f "$context/source-manifest.json" ]] || {
    echo "Incomplete staged context; run prepare-context.py first." >&2; exit 1;
}
jobs=${BUILD_JOBS:-8}
cpus=${BUILD_CPUS:-8}
[[ "$jobs" =~ ^[1-9][0-9]*$ && "$cpus" =~ ^[1-9][0-9]*$ ]] || {
    echo "BUILD_JOBS and BUILD_CPUS must be positive integers" >&2; exit 2;
}
python3 "$context/build/dependencies.py" "${target#*-}" --context "$context" --check
platform=$(docker info --format '{{.OSType}}/{{.Architecture}}')
case "$platform" in linux/x86_64|linux/amd64) ;; *)
    echo "Use a Linux x86_64 Docker daemon; found $platform" >&2; exit 1 ;;
esac
revision=$(cat "$context/source-revision.txt")
image=${IMAGE_TAG:-${IMAGE_PREFIX:-sphinxsim}:$image_suffix}
mkdir -p "$context/logs"
log="$context/logs/$target-$(date -u +%Y%m%dT%H%M%SZ)-$$"
export DOCKER_BUILDKIT=0
# No automatic stop/start of any service, no GPU access needed for compilation.
docker build --network host --memory "${BUILD_MEMORY:-24g}" \
    --cpu-period 100000 --cpu-quota "$((cpus * 100000))" \
    --build-arg SOURCE_REVISION="$revision" --build-arg BUILD_JOBS="$jobs" \
    -t "$image" -f "$recipe" "$context" 2>&1 | tee "$log.log"
docker image inspect "$image" > "$log.image.json"
echo "Built $image; log: $log.log"
