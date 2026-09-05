---
name: visualization
description: "SPHinXsim visualization guidance for preview architecture, rendering modes, annotations, constraints, and testing patterns. Invoke by default for any task that edits or debugs files under sphinxsim/visualization."
---

# SPHinXsim Visualization

## Purpose
Use this skill when working on preview and rendering behavior in SPHinXsim, especially under `sphinxsim/visualization`.

## Invocation Triggers
Invoke this skill by default when any of the following is true:
- The task modifies any file under `sphinxsim/visualization`.
- The task debugs preview behavior, plotting behavior, camera/view mode, labels, or legend content.
- The task touches visualization tests in `tests/test_visualization.py`.

Do not skip this skill for visualization-path changes, even if the user request is brief.

## Scope
Covers:
- Rendering pipeline and fallback behavior
- `ConfigVisualizer` rendering flow
- Annotation/label conventions
- Body constraints and observer visualization
- 2D vs 3D preview behavior
- Visualization testing patterns

Does not cover:
- General C++ solver runtime debugging unrelated to preview
- New project scaffolding
- Non-visualization CLI features

## Architecture

### Rendering pipeline (priority order)
1. VTP mode (preferred)
- C++ `buildGeometries()` generates `.vtp` polygon meshes.
- PyVista loads meshes for rendering.
- Preferred for geometry fidelity.

2. C++ bounds fallback
- If VTP meshes are unavailable, query bounds from `GeometryBuilder.getShapeBounds()`.
- Render bounding boxes as fallback.

Design rule:
- Preview always rebuilds geometry from the current config.
- There is no `use_cpp` / `--no-cpp` switch; fallback to cached bounds is automatic when VTP meshes are not produced.
- Prefer VTP/VTK objects for visualization.
- Avoid constructing ad-hoc meshes from config-only geometry when VTP/VTK path is expected.

## Core files
- `sphinxsim/visualization/preview.py`
- `sphinxsim/visualization/annotations.py`
- `tests/test_visualization.py`

## Key conventions

### `ConfigVisualizer` responsibilities
- Resolve spatial dimension / view mode.
- Populate plotter with shapes, oriented boxes, constraints, observers, and annotations.
- Keep rendering robust when PyVista or mesh assets are unavailable.
- Support screenshot output via `screenshot_path` parameter on `preview()`.
- Rebuild geometry on each preview run and refresh the shape-bounds cache from the builder.

### Annotation functions
Keep annotation formatting centralized in `annotations.py`:
- `body_label(...)`
- `oriented_box_label(...)`
- `observer_label(...)`
- `body_constraint_label(...)`
- `gravity_label(...)`

Guidelines:
- Use compact, readable multiline labels.
- Keep display fields stable to avoid test churn.

### Color palette
Use fixed semantic color constants in `preview.py` for element classes (fluid, solid, continuum, region, observer, constraint, etc.).

## Body constraints rendering pattern
For each `config.body_constraints` item:
- Build label with `body_constraint_label(...)`.
- If constraint has a valid `region` referencing an oriented box:
  - render region overlay (wireframe style, constraint color).
  - place label at overlay center.
- Else:
  - resolve target body shape by name.
  - place label at body mesh center when mesh is available.

## 2D rendering notes
- 2D mode should use orthographic XY-style view behavior.
- Keep controls and camera defaults consistent with dimension inference.
- Ensure labels and overlays remain legible in planar mode.

## Screenshot output
`preview()` accepts an optional `screenshot_path` parameter:
- When provided, forces off-screen rendering (`off_screen = True`).
- Calls `plotter.screenshot(str(screenshot_path))` instead of `plotter.show()`.
- No interactive window is opened.
- CLI exposes this via `--screenshot FILE` / `-s FILE` on both direct and shell `preview` commands.
- Shell mode parses `--screenshot`/`-s` from the input line and passes it through `argparse.Namespace` to `cmd_preview`.
- On success, CLI prints `📸 Screenshot saved to: {path}`.

## Testing playbook
Use `tests/test_visualization.py`.

1. Label unit tests
- Validate generated text content for each annotation function.
- Prefer strict substring assertions for critical fields.

2. Preview rendering tests
- Use mocked PyVista objects and fake plotter methods.
- Assert rendering flow does not raise.
- Assert expected plotting calls when behavior is deterministic.

3. Screenshot tests
- Mock plotter must expose `screenshot()` method and `window_size` attribute.
- Use `__getattr__` fallback on mock plotter to absorb arbitrary method calls (e.g. `add_axes`, `show_grid`, `add_radio_button_widget`).
- Assert `plotter.screenshot()` is called when `screenshot_path` is set, and `plotter.show()` is called otherwise.
- Assert `off_screen` is forced on when `screenshot_path` is provided.
- CLI tests should verify `--screenshot`/`-s` flag passes the path through `argparse.Namespace`.
- Shell tests should verify `--screenshot`/`-s` parsing from the input line.

4. Config validation-aware fixtures
- Build test config via `SimulationConfig(**data)`.
- Respect schema requirements:
  - region oriented box requires `half_size` and `transform`.
  - simbody constraints require `config.restart`.

## Common failure patterns
- Missing import in local `annotations` import block inside `_populate_plotter`.
- Invalid oriented box schema in tests (`center/normal/radius` used for region type).
- Simbody test configs missing restart section.
- Mismatch between expected label text and annotation formatter.
- Mock plotter missing methods called by `preview()` (e.g. `add_axes`, `show_grid`, `add_radio_button_widget`). Use `__getattr__` fallback returning a no-op lambda to avoid whack-a-mole.
- Mock plotter missing `window_size` attribute — this is a property access, not a method call, so `__getattr__` won't catch it. Set it as a class attribute.

## Implementation checklist
When adding a new visualization element:
1. Add or extend schema model/validation if needed.
2. Add annotation helper in `annotations.py`.
3. Add render block in `_populate_plotter` with semantic color constant.
4. Update legend entries.
5. Add label tests and preview tests.
6. Run visualization test file.

When adding output modes (e.g. screenshot, animation):
1. Add parameter to `preview()` method.
2. Wire CLI flag in `cmd_preview()` (direct command subparser).
3. Wire shell-mode parsing in the shell `preview` handler.
4. Update mock plotter classes in tests with `__getattr__` fallback and any needed class attributes.
5. Add tests for the new output path.
6. Update `docs/visualization.md` and `docs/cli-usage.md`.
7. Run visualization test file.

## Verification command
From repo root:

```bash
.venv/bin/python -m pytest tests/test_visualization.py -v
```
