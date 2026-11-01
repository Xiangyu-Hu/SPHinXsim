#!/bin/bash
# Build only. Loading/testing on H20 follows the operator's manual transfer.
set -euo pipefail
desktop_dir=$(cd -- "$(dirname -- "$0")" && pwd)
image=${DESKTOP_IMAGE:-sphinxsim-desktop:0.1.0}
fallback_proxy=${DOWNLOAD_PROXY:-}
mkdir -p "$desktop_dir/.build"

download() {
    local url=$1 filename=$2 checksum=$3
    local target="$desktop_dir/.build/$filename"
    if [[ -f $target ]] && printf '%s  %s\n' "$checksum" "$target" | shasum -a 256 -c - >/dev/null 2>&1; then
        return
    fi
    # Verify the same upstream digest even when a domestic GitHub mirror is used.
    if ! { curl -fL --connect-timeout 10 --max-time 120 \
            "${RELEASE_MIRROR:-https://ghfast.top/}$url" -o "$target.part" \
            && printf '%s  %s\n' "$checksum" "$target.part" | shasum -a 256 -c -; }; then
        echo "Domestic release mirror failed; trying upstream for $filename." >&2
        if ! curl -fL --connect-timeout 10 --max-time 300 --speed-limit 1024 --speed-time 45 \
            "$url" -o "$target.part"; then
            [[ -n $fallback_proxy ]] || return 1
            echo "Direct download failed; retrying $filename through the build-only proxy." >&2
            curl -fL --connect-timeout 10 --max-time 300 --proxy "$fallback_proxy" \
                "$url" -o "$target.part"
        fi
    fi
    printf '%s  %s\n' "$checksum" "$target.part" | shasum -a 256 -c -
    mv "$target.part" "$target"
}
download https://github.com/kasmtech/KasmVNC/releases/download/v1.5.0/kasmvncserver_jammy_1.5.0_amd64.deb \
    kasmvncserver_jammy_1.5.0_amd64.deb 485c190b1d9269fc40fc7205f850ecf71292d737cdb5482e778f2e52f128cd8a
download https://github.com/VirtualGL/virtualgl/releases/download/3.1.5/virtualgl_3.1.5_amd64.deb \
    virtualgl_3.1.5_amd64.deb df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e

docker build --platform linux/amd64 --pull=false --progress plain \
    --build-arg SOURCE_REVISION="$(git -C "$desktop_dir" rev-parse HEAD)" \
    -t "$image" "$@" "$desktop_dir"
docker image inspect "$image" --format '{{.Os}}/{{.Architecture}} {{.Id}}'
