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

The extra includes `pyvistaqt` for responsive persistent preview in interactive
shell mode. PySide6 and QtPy are installed with the base package.

The compiled C++ extension (`_sphinxsys_core_2d` or `_sphinxsys_core_3d`) is
required for preview geometry generation. The preview always attempts to build
VTP meshes first and automatically falls back to cached shape bounds when VTP
files are not produced. If neither source can provide a configured shape, the
preview stops with an error instead of opening an empty scene. Rebuild or update
the SPHinXsim Python binding library if the native geometry is missing; rebuilding
only the C++ projects may leave the Python binding out of date.

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
| Generated particles (`--with-particles`) | Points by default; optional sphere splats | Per-body colour |

Each shape and oriented box is labelled with an annotation that includes:

- **Body shapes**: material type, density, sound speed, thermal boundary type (when applicable).
- **Oriented boxes**: BC type (emitter, bi-directional), inflow speed or pressure, and particle-relaxation constraints targeting that oriented box.
- **Gravity**: shown in the lower-left corner when a gravity vector is defined.

In the Qt-backed shell Preview and desktop session, hovering an annotation
enlarges it. Clicking an annotation reveals and selects its corresponding
configuration entry in the editor; this expands the entry's ancestors and
clears the editor filter if needed to make the entry visible. These annotation
links are not currently connected in the Workbench.

## JSON editor menus

The Qt-backed shell Preview and the [Workbench](workbench.md) use a searchable
**Setting / Value** tree to edit the loaded configuration. Expand a group to
find its fields, or use the filter to narrow the tree. Boolean, numeric and
enumerated values use controls appropriate to their types.

### Right-click a setting

Right-click a tree row to open its context menu. Actions depend on the selected
row; unavailable actions are dimmed. A menu may contain:

| Action | Use |
|---|---|
| **Copy value** | Copy a field or object as a JSON value. |
| **Paste value** | Replace a field or object with a compatible clipboard value. Values are checked against the destination type; fixed-size vectors must have the expected number of components. Plain text is also accepted for string fields. |
| **Add property** | Add an absent schema property to an object. Choose the property from the submenu, then edit its value. |
| **Remove property** | Remove an existing removable property from an object. Required properties cannot be removed this way. |
| **Add** | Add an entry to the selected list. |
| **Add as** | Choose the type of a new list entry when the list supports multiple variants. |
| **Duplicate** / **Remove** | Duplicate or remove the selected list entry. |
| **Move up** / **Move down** | Reorder the selected list entry within its list. |
| **Show annotation** | Show or hide the preview annotation associated with the selected entry, when present. |
| **Show visualization** | Show or hide the rendered preview element associated with the selected entry, when present. |

For example, to duplicate a body, expand the appropriate body list, right-click
the body entry, choose **Duplicate**, and edit the new entry. To add an optional
setting, right-click its parent object and choose it under **Add property**.
Adding or duplicating an entry may require further edits before the configuration
passes validation. The annotation and visualization toggles are enabled only
when the selected entry has a corresponding bound preview element. The shell
Preview and desktop session provide these bindings; the Workbench does not
currently connect them. Errors are reported in the editor status area.

### Tree menu, undo and saving

Click the **Setting ▾** column header to open the tree menu:

- **Expand** and **Collapse** change the tree display.
- **Revert** undoes the latest local edit; `Ctrl+Z` also invokes undo.
- **Reload** reads the configuration from disk. If there are unsaved changes,
  the editor asks before discarding them.

These menu actions change the editor draft, not the file immediately:

- In the **shell Preview**, press `Ctrl+S` to validate and save the loaded JSON
  and rebuild the preview. If validation or saving fails, the status area
  reports the error and the file is not replaced. If saving succeeds but the
  preview rebuild fails, the saved changes remain on disk and the error is
  reported. Refreshing a preview that would discard unapplied edits asks for
  confirmation.
- In the **Workbench**, use **Validate** to check the draft, **Preview draft**
  to visualize valid unsaved edits, or **Save + Preview** (`Ctrl+S`) to save
  and refresh. Invalid edits leave the saved file and last valid scene intact.
  Switching between the editor and Chat preserves the draft.

When possible, saving preserves the existing JSON layout, numeric spelling and
line endings rather than adding unchanged schema defaults.

## Particle legend context menu

In the Qt-backed shell Preview or Workbench canvas, right-click a body's
**Particles** entry in the legend. Right-clicking and dragging remains a
canvas gesture; use a click without dragging to open the menu.

### Show or hide particles

Toggle **Show particles** to hide or show that body's particle cloud. Its
legend entry remains visible with a dimmed swatch when hidden, so it can be
shown again. Hiding particles also hides that body's color bar. This affects
only the display: it does not change the simulation or saved VTP files.

### Particle representation

Choose **Representation → Sphere splats** in a body's particle legend menu to
render shaded, circular splats like ParaView's Sphere preset. **Points** is the
default and restores the original display. The choice applies to that body and
persists across its live and replayed frames in the same scene.

The splat radius is half the configured particle spacing. If the spacing is
unspecified, it is estimated from the particle cloud's bounds and point count.
Rendering cost depends on the particle count, spacing, and graphics hardware.
If spacing cannot be estimated for a later frame, that frame uses points and
logs a warning; the splat selection is retried on subsequent frames. The
selection resets when changing scenes.

### Particle transparency

Choose **Transparency → Opaque (0%)** or **5% transparent** for each body.
The default is 5% transparent for both points and sphere splats. The choice
persists across live and replayed frames in the same scene and resets when
changing scenes.

### Color particles by an output attribute

Particles are colored by body by default. In the shell, use
`preview --with-particles` followed by `initialize-simulation` to produce and
display initial solver output. Then right-click a body's **Particles** legend
entry and choose **Color by**, followed by an attribute such as **Pressure**
or **Velocity magnitude**. The Workbench offers the same menu when displaying
solver output. Generated-particle previews have the visibility option but do
not yet offer **Color by**; their representation and transparency can still be
changed.

The submenu lists numeric scalar and vector point-data arrays present in that
body's displayed solver snapshot, including integer arrays such as particle
IDs. Vectors are colored by magnitude; matrices are not offered. The color bar
identifies the body and variable. Select **Body color** to restore the default.

Right-click a visible color bar to choose **Blue to red** (the default),
**Viridis**, or **Grayscale**. The palette applies to that body, including
points and sphere splats, and persists across its fields and frames in the same
scene. Changing the palette does not change the color range.

For an attribute with a valid saved [scaling reference](#particle-scaling-metadata),
scalar colors use the fixed range `[-reference, reference]` and vector
magnitudes use `[0, reference]`; values outside the range use the end colors.
Without a valid reference, including in older files, the range is calculated
from each frame's finite values and may change between frames. If the selected
attribute is absent or has no finite values, the body temporarily uses its
body color. The selection is retained and applied again when the attribute
returns.

Visibility and color selections persist as frames change during a run and
shell replay. Switching to another case or run resets them. The preview also
shows the physical time stored in the displayed solver snapshots; if bodies
are displaying different times, the overlay shows the latest physical time.
Generated-particle iterations are not simulation times.

## CLI usage

### Basic preview (direct command)

```bash
sphinxsim preview path/to/config.json
```

This opens an interactive PyVista window showing the geometry. The preview
writes a temporary runtime JSON from the validated configuration and uses it
to build geometry; it does not modify the source JSON.

### Preview with generated particles

```bash
sphinxsim preview path/to/config.json --with-particles
```

Also runs particle generation (`generateParticles()`) and overlays the latest
generated particle cloud for each particle-generation body.  This is opt-in
because particle generation can be expensive.

When particles are shown:

- Regular geometry shapes are hidden to keep the view clear.
- Oriented boxes, constraints, observers, gravity, and all annotations remain visible.
- One particle cloud is rendered per body, using the highest step VTP file
  (`BodyName_<step>.vtp` or `BodyName_ite_<step>.vtp`).
- The legend identifies each particle cloud by body name. Solver snapshots
  displayed during a live run or replay also show their physical time.

This is useful for verifying particle distribution and spacing before running
the full simulation.

### Particle scaling metadata

Particle VTP files produced by SPHinXsim include a `Float64` field-data value
named `ScalingReference_<variable>` for each recorded variable with a known
physical scaling reference (for example, `ScalingReference_Pressure` and
`ScalingReference_Velocity`). The position reference is stored as
`ScalingReference_Position`. These are characteristic magnitudes derived from
the configuration's characteristic dimensions, not measured data ranges.
Known dimensionless variables have a reference of `1`; unknown variables and
particle IDs have no reference metadata. Without configured scaling, known
references are `1`.

Particle values in the files remain in physical units; the references are
metadata and must not be applied to those values again. Older VTP files without
these fields remain readable. This metadata does not change the default
per-body particle colouring.

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

In shell mode, `preview` keeps a persistent window and returns control to the
shell prompt. Running `preview` again updates the existing window and rebuilds
geometry from the current config.

Notes:
- `preview --screenshot ...` is one-shot and does not use the persistent window.
- Persistent interactive mode relies on `pyvistaqt` and the base package's
  PySide6 Qt backend.
- See [JSON editor menus](#json-editor-menus) and [Particle legend context
  menu](#particle-legend-context-menu) for editing and display controls.
- Loading a different configuration and running `preview` rebinds the editor
  to that file. If the persistent window is closed, running `preview` again
  creates a fresh window and reattaches the editor.

```
sphinxsim> load config.json
✅ Loaded config from config.json

sphinxsim> preview
🖼  Building configuration preview for: .../config.json
   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.
✅ Preview used C++ geometry (VTP meshes).

sphinxsim> preview --screenshot preview.png
🖼  Building configuration preview for: .../config.json
   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.
✅ Preview used C++ geometry (VTP meshes).
📸 Screenshot saved to: preview.png

sphinxsim> preview --with-particles
🖼  Building configuration preview for: .../config.json
   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.
   Particle generation overlay is enabled (--with-particles).
✅ Preview used C++ geometry (VTP meshes).
▶️ Replaying 100 particle frames at up to 25 FPS.

sphinxsim> initialize-simulation
✅ Simulation built
✅ Simulation initialized

sphinxsim> continue-to-run
🚀 Running simulation...
ℹ️ Preview checks for the latest body snapshots every 75 ms.
✅ Simulation completed successfully!
```

For the full preview-and-animate workflow, load a JSON config and run:

```text
sphinxsim> run --with-animation
```

This performs `preview --with-particles`, `initialize-simulation`, and
`continue-to-run` on the same generated simulation. Plain `run` remains unchanged. Once the animated
run or a manual `continue-to-run` completes, enter `replay` to play its
recorded simulation snapshots again at up to 25 FPS:

```text
sphinxsim> replay
▶️ Replaying 100 particle frames at up to 25 FPS.
```

If needed, replay opens the regular configuration preview, preserving the
correct 2D/3D camera (including the orthographic XY view for 2D). Wait until
`continue-to-run` has finished before starting replay. Replay keeps the current
camera framing fixed while the particle snapshots animate.

`preview` requires a config to be loaded first (via `load` or `generate`).
After particle generation, `preview --with-particles` automatically plays
relaxation snapshots (`_ite_` files) in the Qt-backed preview. To play them
again, including before initialization, enter `replay --relaxation`. If Qt
playback is unavailable, the preview remains usable and reports a warning.
Simulation playback and plain `replay` exclude relaxation snapshots.
All playback reads the shared output directory, so older files of the same
kind may also be included.

After `preview --with-particles`, the shell retains the generated-particle
`SPHSimulation` instance and its temporary runtime config. The next command,
`initialize-simulation`, builds and initializes that same instance, writes the
initial body-state snapshots, and displays them without running the solver.
`continue-to-run` then runs the initialized instance without generating
particles or initializing again. `replay --relaxation` also preserves the
instance, and initialization stops any active relaxation playback. Other shell
commands discard the retained instance and its temporary config; use `run` when a fresh simulation from the
saved config is intended. If displaying the initialized snapshots fails, the
error is reported and the initialized simulation remains available for
`continue-to-run`.

`continue-to-run` checks for new snapshots every 75 ms. At each check, the
preview looks up the latest complete body-state VTP
for every body and refreshes any body with a newer file. It does not queue
older frames for playback, so the view stays close to the current simulation
state. Incomplete files are retried on the next check. This uses `pyvistaqt`
with the base package's PySide6 Qt backend. If a persistent preview cannot be
opened, the simulation still continues but the window cannot refresh its
particles live.

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
| `with_particles` | `bool` | `False` | Run particle generation and overlay latest particles per body. In shell persistent mode, enabling this always redraws the scene. |
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

### VTP geometry (preferred)

The visualizer always:

1. Passes your original JSON file directly to `SPHSimulation` (no copy written).
2. Calls `SPHSimulation.buildGeometries()` from the C++ extension.
3. The C++ builders write `Shape<Name>.vtp` files to `.build-temp/preview_geometry/output/`.
4. PyVista reads and renders those polygon meshes.

This gives **accurate geometry** — including rotations, boolean-composition results,
and imported triangle meshes.

### Bounds fallback

When VTP files are not produced, the visualizer queries `getShapeBounds()` from
the geometry builder and renders axis-aligned bounding boxes from the cached
shape bounds.

Both paths require the C++ extension. If geometry generation fails, or a shape
has neither a readable VTP mesh nor cached bounds, the preview reports an error
and does not show a partial/empty scene. If the extension is missing or
incompatible, rebuild and reinstall the SPHinXsim Python binding as well as the
C++ libraries.

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
- **Initial condition colouring**: colour-mapping particle regions by their initial temperature, velocity, or density.
- **BC arrow glyphs**: directional arrows for inflow/outflow boundaries.
- **Embedded notebook support**: inline rendering for Jupyter environments.

## See also

- [CLI Usage](cli-usage.md) for the full command reference
- [Installation](installation.md) for build requirements including VTK/PyVista
