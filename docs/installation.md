# Installation Guide

Choose your platform-specific setup guide:

- [Linux Installation](installation-linux.md)
- [Windows Installation](installation-windows.md)
- [macOS Installation](installation-macos.md)

## Shared Notes

- Use CMake presets from the repository root.
- Keep vcpkg outside the repository folder (for example, `../vcpkg`).
- Preferred workflow: use `pip install` from source so Python packaging and C++ extension build happen in one step.
- Use pip's verbose mode to see the CMake/Ninja build output and completed/total build steps while the wheel is being built. The native build output does not provide a reliable time estimate.

```bash
# Linux/macOS
CMAKE_ARGS="-D CMAKE_TOOLCHAIN_FILE=$(pwd)/../vcpkg/scripts/buildsystems/vcpkg.cmake" \
python3 -m pip install -v -e ".[dev,visualization]"

# Windows (PowerShell)
$env:CMAKE_ARGS='-D CMAKE_TOOLCHAIN_FILE="' + "$pwd\..\vcpkg\scripts\buildsystems\vcpkg.cmake" + '" -D CMAKE_C_COMPILER=cl -D CMAKE_CXX_COMPILER=cl'
python -m pip install -v -e ".[dev,visualization]"
```

The Windows CI installation omits the optional CAD extra; see the
[Windows guide](installation-windows.md) for native DLL conflict details.

- Install Python dev dependencies before running tests:

```bash
python -m pip install -e ".[dev]"
```

- To enable the `sphinxsim preview` visualizer, install the optional PyVista dependency:

```bash
python -m pip install -e ".[visualization]"
```

- Optional: to enable high-quality ray-traced rendering via `--renderer cycles`, install [Blender](https://www.blender.org/) (3.6 LTS or 4.x+) and ensure `blender` is on `PATH` (or set `SPHINXSIM_BLENDER_EXECUTABLE=/path/to/blender`). Exporting portable Cycles scene bundles (`--save-bundle DIR`) works out of the box with base `sphinxsim` and does not require Blender or PyVista on the host machine.

  See [Visualization](visualization.md) for details.

- Optional: enable CLI shell completion.

```bash
# bash
echo 'eval "$(sphinxsim --generate-completion bash)"' >> ~/.bashrc

# zsh
echo 'eval "$(sphinxsim --generate-completion zsh)"' >> ~/.zshrc

# fish
echo 'sphinxsim --generate-completion fish | source' >> ~/.config/fish/config.fish
```

- Troubleshooting shell completion:

```bash
# Reload startup config in the current terminal
source ~/.bashrc      # bash
source ~/.zshrc       # zsh
source ~/.config/fish/config.fish  # fish
```

```zsh
# zsh completion must be initialized
autoload -Uz compinit
compinit
eval "$(sphinxsim --generate-completion zsh)"
```

```bash
# Quick sanity check that completion script is produced
sphinxsim --generate-completion bash | head
```

- Run full Python tests (including examples):

```bash
python -m pytest tests/ examples/ -v
```
