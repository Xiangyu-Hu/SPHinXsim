# Visualization

SPHinXsim includes a pre-run configuration visualizer that renders your simulation
setup — geometries, bodies, boundary conditions, and physics annotations — before
the expensive C++ solver starts.  This lets you catch setup mistakes early and build
an intuitive picture of what the simulation will run.

## Requirements

PyVista is required:

```bash
pip install sphinxsim[visualization]
```

The compiled C++ extension (`_sphinxsys_core_2d` or `_sphinxsys_core_3d`) is
required only for C++ geometry rendering (simulation shapes and oriented-box
meshes). Without it (or when using `--no-cpp`), preview still opens and
renders only the system domain bounding box and annotations.

## What it shows

| Element | Visual style | Colour |
|---|---|---|
| Fluid body shapes | Solid surface | Blue |
| Solid body shapes | Solid surface | Grey |
| Continuum body shapes | Solid surface | Amber |
| Other defined shapes | Wireframe | Green |
| Inlet/Outlet oriented boxes | Wireframe | Red |
| Constraint region oriented boxes | Wireframe | Yellow |
| System domain bounding box | Wireframe | White (10 % opacity) |

Each shape and oriented box is labelled with an annotation that includes:

- **Body shapes**: material type, density, sound speed, thermal boundary type (when applicable).
- **Oriented boxes**: BC type (emitter, bi-directional), inflow speed or pressure, and particle-relaxation constraints targeting that oriented box.
- **Gravity**: shown in the lower-left corner when a gravity vector is defined.

## CLI usage

### Basic preview (direct command)

```bash
sphinxsim preview path/to/config.json
```

This opens an interactive PyVista window showing the geometry.  The original
JSON file is passed directly to the C++ `SPHSimulation` — it is the single
source of truth, no intermediate copy is written.

### Skip C++ geometry build

```bash
sphinxsim preview path/to/config.json --no-cpp
```

Skips the C++ geometry build entirely.  Shapes are not rendered; only the
system domain bounding box and annotations are shown.

### Off-screen rendering

```bash
sphinxsim preview path/to/config.json --off-screen
```

Renders to an off-screen buffer instead of opening a window.  Intended for
automated testing or headless environments.

### Screenshot output

```bash
sphinxsim preview path/to/config.json --screenshot preview.png
# or equivalently:
sphinxsim preview path/to/config.json -s preview.png
```

Saves a screenshot of the preview to the specified file instead of opening an
interactive window.  This automatically enables off-screen rendering, so
`--off-screen` is implied and does not need to be passed separately.

Supported formats depend on your PyVista/VTK build, but typically include PNG,
JPEG, and TIFF.

```bash
sphinxsim preview path/to/config.json -s preview.png
📸 Screenshot saved to: preview.png
```

This is useful for:
- Generating preview images for reports or documentation.
- Batch-generating previews in CI pipelines.
- Headless environments where no display is available.

### Interactive shell

`preview` is available as a first-class shell command:

```
sphinxsim> load config.json
✅ Loaded config from config.json

sphinxsim> preview
🖼  Building configuration preview for: .../config.json
   Attempting C++ geometry build for accurate VTP meshes...
✅ Preview used C++ geometry (VTP meshes).

sphinxsim> preview --no-cpp
🖼  Building configuration preview for: .../config.json
   Skipping C++ geometry build (--no-cpp).
ℹ️ Preview rendered without C++ geometry (--no-cpp).

sphinxsim> preview --screenshot preview.png
🖼  Building configuration preview for: .../config.json
   Attempting C++ geometry build for accurate VTP meshes...
✅ Preview used C++ geometry (VTP meshes).
📸 Screenshot saved to: preview.png
```

`preview` requires a config to be loaded first (via `load` or `generate`).

## Python API

```python
from pathlib import Path
from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.visualization.preview import ConfigVisualizer
import json

config_path = Path("my_config.json")
config = SimulationConfig(**json.loads(config_path.read_text()))

viz = ConfigVisualizer(config, project_root=Path("."), config_path=config_path)
viz.preview()                     # opens interactive window
viz.preview(use_cpp=False)        # skip C++ build (shapes not rendered)
viz.preview(title="My setup")     # custom window title
viz.preview(screenshot_path="preview.png")  # save screenshot, no window
```

### `ConfigVisualizer` parameters

| Parameter | Type | Description |
|---|---|---|
| `config` | `SimulationConfig` | Validated Pydantic config object |
| `project_root` | `Path` | Root of the SPHinXsim project (locates `.build-temp/`) |
| `config_path` | `Path \| None` | Path to the original JSON file — passed directly to C++. Required for geometry rendering. |
| `off_screen` | `bool` | Render off-screen when `True` (default `False`) |

### `preview()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `title` | `str` | `"SPHinXsim - Configuration Preview"` | Window title |
| `use_cpp` | `bool` | `True` | Run C++ geometry build. Raises `ImportError` if extension not installed. |
| `screenshot_path` | `str \| Path \| None` | `None` | When set, saves a screenshot to this path instead of opening a window. Implies off-screen rendering. |

After rendering, the CLI reports which tier was used:
- `✅ Preview used C++ geometry (VTP meshes).`
- `ℹ️ Preview used C++ bounds fallback (no VTP meshes produced).`

### Inspecting which tier was used

```python
viz.preview()
print(viz.used_cpp_geometry)   # True if VTP meshes were rendered
print(viz.used_cpp_bounds)     # True if live C++ bounds were used
```

## Two-tier rendering strategy

### Tier 1 — VTP geometry (preferred)

When `use_cpp=True` (the default), the visualizer:

1. Passes your original JSON file directly to `SPHSimulation` (no copy written).
2. Calls `SPHSimulation.buildGeometries()` from the C++ extension.
3. The C++ builders write `Shape<Name>.vtp` files to `.build-temp/preview_geometry/output/`.
4. PyVista reads and renders those polygon meshes.
5. The live `SPHSimulation` object is kept in memory for Tier 2 queries.

This gives **accurate geometry** — including rotations, boolean-composition results,
and imported triangle meshes.

### Tier 2 — C++ shape bounds (fallback)

When VTP files are not produced (e.g. the builder does not write VTPs for a
given shape type), the visualizer queries `getShapeBounds()` directly from the
live `SPHSimulation` object and renders axis-aligned bounding boxes.

Both tiers require the C++ extension.  If it is not installed, an
`ImportError` is raised with an install hint.

## Shape types and VTP availability

Not all shape types produce VTP files in the C++ builders.  Here is the
complete mapping:

| Shape type | VTP written by C++? |
|---|---|
| `bounding_box` | ✅ Yes |
| `box` (with transform/rotation) | ✅ Yes |
| `expanded_box` | ✅ Yes |
| `multipolygon` (2D) | ✅ Yes |
| `triangle_mesh` (3D) | ✅ Yes |
| `complex_shape` | ❌ No (boolean composition of named sub-shapes) |

`complex_shape` geometries are skipped in the VTP pass; their constituent
sub-shapes are rendered individually via Tier 2 bounds.

## Annotations module

The `sphinxsim.visualization.annotations` module provides standalone label
generators that can be used outside of PyVista:

```python
from sphinxsim.visualization.annotations import body_label, oriented_box_label, gravity_label

# Human-readable body summary
label = body_label("WaterBody", config)
# → "Fluid: WaterBody\nρ=1000.0\nc=10.0"

# Oriented box with BC annotation
ob = config.geometries.oriented_boxes[0]
label = oriented_box_label(ob, config)
# → "Inlet [in_outlet]\nBC → WaterBody: emitter v=1.5"

# Gravity vector
g = gravity_label(config)
# → "g = (0.0, -9.81)"  or None if not set
```

## Future work

The visualization module is intentionally minimal for this phase.  Planned extensions include:

- **SDF / level-set geometry**: rendering signed-distance-function shapes once those are added to the geometry builder.
- **Particle cloud preview**: optional overlay of generated particle positions after `generateParticles()`.
- **Initial condition colouring**: colour-mapping particle regions by their initial temperature, velocity, or density.
- **BC arrow glyphs**: directional arrows for inflow/outflow boundaries.
- **Embedded notebook support**: inline rendering for Jupyter environments.

## See also

- [CLI Usage](cli-usage.md) for the full command reference
- [Installation](installation.md) for build requirements including VTK/PyVista
