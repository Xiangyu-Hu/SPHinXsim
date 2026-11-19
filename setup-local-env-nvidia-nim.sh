# Local development environment setup
# Source this file to set up vcpkg environment for local development

# Prevent Python from creating __pycache__ directories
export PYTHONDONTWRITEBYTECODE=1

# Ensure local sphinxsim package is importable from any working directory.
_SPHINXSIM_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${PYTHONPATH:-}" ]; then
    export PYTHONPATH="$_SPHINXSIM_REPO_ROOT"
else
    export PYTHONPATH="$_SPHINXSIM_REPO_ROOT:$PYTHONPATH"
fi

# Provide a fallback launcher if the console script is not installed on PATH.
if ! command -v sphinxsim >/dev/null 2>&1; then
    if command -v python >/dev/null 2>&1; then
        sphinxsim() { python -m sphinxsim "$@"; }
        echo "ℹ️ sphinxsim command not found on PATH; using python -m sphinxsim fallback"
    elif command -v python3 >/dev/null 2>&1; then
        sphinxsim() { python3 -m sphinxsim "$@"; }
        echo "ℹ️ sphinxsim command not found on PATH; using python3 -m sphinxsim fallback"
    else
        echo "❌ Neither python nor python3 was found in PATH"
    fi
fi

# Set LLM provider for local development
export SPHINXSIM_LLM_PROVIDER=nvidia_nim
export NVIDIA_NIM_MODEL="z-ai/glm-5.2"
export NVIDIA_NIM_BASE_URL="https://integrate.api.nvidia.com/v1"
# Comma-separated alternatives tried automatically if the primary model is degraded/unavailable.
export NVIDIA_NIM_FALLBACK_MODELS="meta/llama-3.1-70b-instruct,meta/llama-3.1-8b-instruct"

# API key loading order:
# 1) Existing NVIDIA_NIM_API_KEY in environment
# 2) Existing NVIDIA_API_KEY in environment (compatibility)
# 3) First line from NVIDIA_NIM_API_KEY_FILE (default: ~/.config/sphinxsim/nvidia_nim_api_key)
export NVIDIA_NIM_API_KEY_FILE="${NVIDIA_NIM_API_KEY_FILE:-$HOME/.config/sphinxsim/nvidia_nim_api_key}"

if [ -z "${NVIDIA_NIM_API_KEY:-}" ] && [ -n "${NVIDIA_API_KEY:-}" ]; then
    export NVIDIA_NIM_API_KEY="$NVIDIA_API_KEY"
fi

if [ -z "${NVIDIA_NIM_API_KEY:-}" ] && [ -f "$NVIDIA_NIM_API_KEY_FILE" ]; then
    export NVIDIA_NIM_API_KEY="$(head -n 1 "$NVIDIA_NIM_API_KEY_FILE" | tr -d '\r')"
fi

if [ -n "${NVIDIA_NIM_API_KEY:-}" ]; then
    echo "✅ NVIDIA NIM API key loaded"
else
    echo "❌ NVIDIA NIM API key not found"
    echo "Set NVIDIA_NIM_API_KEY, NVIDIA_API_KEY, or create: $NVIDIA_NIM_API_KEY_FILE"
fi

# Set VCPKG_ROOT for local development
export VCPKG_ROOT="$HOME/vcpkg"

# Verify vcpkg is available
if [ -f "$VCPKG_ROOT/vcpkg" ]; then
    echo "✅ vcpkg found at $VCPKG_ROOT"
else
    echo "❌ vcpkg not found at $VCPKG_ROOT"
    echo "Please adjust VCPKG_ROOT or install vcpkg"
fi

# Show available presets
echo ""
echo "Available build presets:"
echo "  cmake --preset integrated-build     # Build everything together"
echo "  cmake --preset python-binding-release # Build with pre-built SPHinXsys"
echo "  cmake --preset simple-binding       # Simple test binding"
