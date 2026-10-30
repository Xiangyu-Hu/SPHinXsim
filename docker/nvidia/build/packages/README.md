# Vendor dependency provenance

Installers and archives are not committed or distributed by this source bundle.
Obtain them from the vendor, subject to the vendor's terms, and supply a local
directory with `dependencies.py --cache DIRECTORY` or `PACKAGE_CACHE=DIRECTORY`.
Every file is checked against `dependencies.lock.json` before use.

CUDA 12 requires `oneapi-for-nvidia-gpus-2025.2.0-linux.sh`:

- Size: 318,643 bytes.
- SHA-256: `84c0225131da6d066144a56ab3bd6213314a894ef4346d400140185b2f97bdc7`.
- [Vendor acquisition instructions](https://developer.codeplay.com/products/oneapi/nvidia/2025.2.0/guides/get-started-guide-nvidia.html).
- `LICENSE_oneAPI_for_nvidia_GPUs.md` preserves the installer library's included
  Apache 2.0 with LLVM Exceptions notice. It does not replace the terms of other
  compiler components or images.

The old Codeplay download has no pinned public URL in the lock. If that exact
version cannot be obtained, use the CUDA 13 recipe on a compatible driver, or
qualify a different toolchain and update its lock explicitly. Do not substitute
an unchecked installer. Other public archives can be fetched with `--download`.
Installers execute only inside Docker build stages, never in the download helper.
