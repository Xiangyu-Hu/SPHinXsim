"""Preview runtime for the SPHinXsim CLI.

This module owns the persistent shell preview window and its live-particle
update loop. The legacy ``sphinxsim.cli.main`` entrypoint keeps a compatibility
alias so older imports continue to work while the implementation is migrated.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import warnings
from pathlib import Path
from threading import Thread
from typing import Any

from pydantic import ValidationError

from .common import PROJECT_ROOT, _write_validated_config
from .json_editor import install_json_editor
from sphinxsim.config.schemas import SimulationConfig

_LIVE_PREVIEW_MAX_FPS = 25


def _config_spatial_dim(config: SimulationConfig) -> int:
    """Infer whether a validated config should use the 2-D or 3-D native module."""
    geo = config.geometries

    if geo.system_domain is not None:
        return len(geo.system_domain.lower_bound)

    if config.gravity is not None:
        return len(config.gravity)

    for constraint in config.body_constraints:
        if constraint.type.value == "simbody":
            if (constraint.mobilized_body or "").lower() == "planar":
                return 2
            if constraint.velocity is not None:
                return len(constraint.velocity)

    for shape in geo.shapes:
        for vec in (shape.lower_bound, shape.upper_bound, shape.half_size):
            if vec is not None:
                return len(vec)
        if shape.transform is not None:
            return len(shape.transform.translation)
        if shape.translation is not None:
            return len(shape.translation)

    for oriented_box in geo.oriented_boxes:
        for vec in (oriented_box.center, oriented_box.normal, oriented_box.half_size):
            if vec is not None:
                return len(vec)
        if oriented_box.transform is not None:
            return len(oriented_box.transform.translation)

    return 3


class _ShellPreviewRuntime:
    """Persistent shell preview runtime.

    Keeps a non-blocking plotter window alive across ``preview`` commands and
    updates its scene in place.
    """

    def __init__(self, *, config_type=None, write_config=None) -> None:
        self.config_type = config_type or SimulationConfig
        self.write_config = write_config or _write_validated_config
        self.plotter: Any | None = None
        self.last_signature: str | None = None
        self._using_background_plotter = False
        self._hover_interactor: Any | None = None
        self._hover_observer_tag: Any | None = None
        self._json_editor: Any | None = None
        self._pending_simulation: Any | None = None
        self._pending_runtime_config_path: Path | None = None
        self._live_run_thread: Thread | None = None
        self._live_refresh_timer: Any | None = None
        self._live_output_dir: Path | None = None
        self._live_particle_body_names: set[str] = set()
        self._live_particle_colours: dict[str, Any] = {}
        self._live_particle_versions: dict[str, float] = {}
        self._live_particle_file_states: dict[Path, tuple[int, int]] = {}
        self._live_playback_queue: list[tuple[float, dict[str, Path]]] = []
        self._live_refresh_interval_ms = round(10.0 * 1000)
        self._live_replay_mode = False
        self._animation_output_dir: Path | None = None
        self._animation_particle_body_names: set[str] = set()
        self._animation_particle_colours: dict[str, Any] = {}
        self._animation_config: SimulationConfig | None = None
        self._animation_config_path: Path | None = None
        self._animation_run_completed = False

    def _reset_preview_state(self) -> None:
        """Forget all state associated with a closed preview window."""
        self._remove_hover_observer()
        self._stop_live_refresh()
        self.discard_pending_simulation()
        self.plotter = None
        self._json_editor = None
        self.last_signature = None
        self._using_background_plotter = False

    def _on_preview_window_closed(self, *_args: Any) -> None:
        """Handle a user closing the Qt window outside the shell command loop."""
        self._reset_preview_state()

    def _watch_preview_window_close(self) -> None:
        """Synchronize pyvistaqt's native close signal with this runtime."""
        if self.plotter is None:
            return
        app_window = getattr(self.plotter, 'app_window', None)
        signal_close = getattr(app_window, 'signal_close', None)
        if signal_close is None:
            return
        try:
            signal_close.connect(self._on_preview_window_closed)
        except Exception:
            pass

    def close(self) -> None:
        plotter = self.plotter
        if plotter is not None:
            try:
                plotter.close()
            except Exception:
                pass
        self._reset_preview_state()

    def discard_pending_simulation(self) -> None:
        """Release a simulation retained by ``preview --with-particles``."""
        self._pending_simulation = None
        runtime_config_path = self._pending_runtime_config_path
        self._pending_runtime_config_path = None
        if runtime_config_path is not None:
            try:
                runtime_config_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _stop_live_refresh(self) -> None:
        timer = self._live_refresh_timer
        self._live_refresh_timer = None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._live_output_dir = None
        self._live_particle_body_names = set()
        self._live_particle_colours = {}
        self._live_particle_versions = {}
        self._live_particle_file_states = {}
        self._live_playback_queue = []
        self._live_replay_mode = False

    def _refresh_live_particles(self) -> None:
        if self.plotter is None or self._live_output_dir is None:
            return

        from sphinxsim.visualization.preview import _PARTICLE_POINT_SIZE, _particle_label

        try:
            import pyvista as pv
        except ImportError:
            return

        if self._live_replay_mode and not self._live_playback_queue:
            self._stop_live_refresh()
            return

        if not self._live_replay_mode and not self._live_playback_queue:
            batch: dict[float, dict[str, Path]] = {}
            has_unstable_snapshot = False
            for vtp_path in self._live_output_dir.glob('*.vtp'):
                for body_name in self._live_particle_body_names:
                    prefix = f'{body_name}_'
                    if not vtp_path.stem.startswith(prefix):
                        continue
                    suffix = vtp_path.stem[len(prefix):]
                    if suffix.startswith('ite_'):
                        suffix = suffix[len('ite_'):]
                    try:
                        sequence = float(suffix)
                    except ValueError:
                        break
                    if sequence <= self._live_particle_versions.get(body_name, float('-inf')):
                        break
                    try:
                        stat = vtp_path.stat()
                    except OSError:
                        break
                    file_state = (stat.st_size, stat.st_mtime_ns)
                    if self._live_particle_file_states.get(vtp_path) != file_state:
                        self._live_particle_file_states[vtp_path] = file_state
                        has_unstable_snapshot = True
                        break
                    batch.setdefault(sequence, {})[body_name] = vtp_path
                    break

            self._live_playback_queue = [
                (sequence, body_snapshots)
                for sequence, body_snapshots in sorted(batch.items())
            ]
            for sequence, body_snapshots in self._live_playback_queue:
                for body_name in body_snapshots:
                    self._live_particle_versions[body_name] = sequence

            timer = self._live_refresh_timer
            if self._live_playback_queue and timer is not None:
                timer.setInterval(round(1000 / _LIVE_PREVIEW_MAX_FPS))
            elif not self._live_playback_queue:
                thread = self._live_run_thread
                if thread is not None and not thread.is_alive() and not has_unstable_snapshot:
                    self._stop_live_refresh()
                    self._live_run_thread = None
                return

        _, body_snapshots = self._live_playback_queue.pop(0)
        for body_name, vtp_path in body_snapshots.items():
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='.*vtkXMLPolyDataReader.*')
                    mesh = pv.read(str(vtp_path))
            except Exception:
                continue
            if getattr(mesh, 'n_points', None) == 0:
                continue
            self.plotter.add_mesh(
                mesh,
                name=f'particle-preview-{body_name}',
                color=self._live_particle_colours.get(body_name),
                opacity=0.95,
                style='points',
                point_size=_PARTICLE_POINT_SIZE,
                label=_particle_label(body_name),
            )

        if self._live_replay_mode and not self._live_playback_queue:
            self._stop_live_refresh()
            print('✅ Particle animation replay completed.')
        elif not self._live_playback_queue:
            timer = self._live_refresh_timer
            if timer is not None:
                timer.setInterval(self._live_refresh_interval_ms)

            thread = self._live_run_thread
            if thread is not None and not thread.is_alive():
                self._stop_live_refresh()
                self._live_run_thread = None

    def _start_live_refresh(self, refresh_interval: float) -> None:
        timer = self._live_refresh_timer
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._live_refresh_timer = None
        self._live_particle_versions = {}
        self._live_particle_file_states = {}
        self._live_playback_queue = []
        self._live_replay_mode = False
        self._live_refresh_interval_ms = round(refresh_interval * 1000)
        if not self._using_background_plotter or self.plotter is None:
            return
        try:
            from qtpy import QtCore
        except ImportError:
            return
        timer = QtCore.QTimer()
        timer.setInterval(round(refresh_interval * 1000))
        timer.timeout.connect(self._refresh_live_particles)
        timer.start()
        self._live_refresh_timer = timer
        if self._live_output_dir is not None:
            for vtp_path in self._live_output_dir.glob('*.vtp'):
                try:
                    stat = vtp_path.stat()
                except OSError:
                    continue
                self._live_particle_file_states[vtp_path] = (stat.st_size, stat.st_mtime_ns)

    def replay_animation(self) -> int:
        """Replay all recorded particle snapshots at no more than 25 FPS."""
        thread = self._live_run_thread
        if thread is not None and thread.is_alive():
            print('❌ Wait for the simulation to finish before replaying.', file=sys.stderr)
            return 1
        if not self._animation_run_completed:
            print('❌ Complete a successful animated run before replaying.', file=sys.stderr)
            return 1
        if self._animation_output_dir is None or not self._animation_particle_body_names:
            print(
                "No particle animation is available. Run 'run --with-animation', "
                "or run 'preview --with-particles' followed by 'continue-to-run'.",
                file=sys.stderr,
            )
            return 1
        if self.plotter is None:
            if self._animation_config is None or self._animation_config_path is None:
                print('❌ The configuration for this particle animation is unavailable.', file=sys.stderr)
                return 1
            rc = self.show_or_update(
                self._animation_config,
                resolved_config_path=self._animation_config_path,
                with_particles=False,
                force=True,
            )
            if rc != 0:
                return rc
        elif not self._using_background_plotter:
            print('❌ Animation replay requires the Qt-backed persistent preview.', file=sys.stderr)
            return 1

        try:
            from qtpy import QtCore
        except ImportError as exc:
            print(
                f'❌ Animation replay requires Qt (`pyvistaqt` with `PySide6` or `PyQt5`): {exc}',
                file=sys.stderr,
            )
            return 1

        snapshots: dict[float, dict[str, Path]] = {}
        for vtp_path in self._animation_output_dir.glob('*.vtp'):
            for body_name in self._animation_particle_body_names:
                prefix = f'{body_name}_'
                if not vtp_path.stem.startswith(prefix):
                    continue
                suffix = vtp_path.stem[len(prefix):]
                if suffix.startswith('ite_'):
                    suffix = suffix[len('ite_'):]
                try:
                    sequence = float(suffix)
                except ValueError:
                    break
                snapshots.setdefault(sequence, {})[body_name] = vtp_path
                break

        if not snapshots:
            print(f'❌ No particle snapshots found in {self._animation_output_dir}.', file=sys.stderr)
            return 1

        self._stop_live_refresh()
        self._live_output_dir = self._animation_output_dir
        self._live_particle_body_names = set(self._animation_particle_body_names)
        self._live_particle_colours = dict(self._animation_particle_colours)
        self._live_particle_versions = {
            body_name: float('-inf')
            for body_name in self._live_particle_body_names
        }
        self._live_playback_queue = [
            (sequence, body_snapshots)
            for sequence, body_snapshots in sorted(snapshots.items())
        ]
        self._live_replay_mode = True
        timer = QtCore.QTimer()
        timer.setInterval(round(1000 / _LIVE_PREVIEW_MAX_FPS))
        timer.timeout.connect(self._refresh_live_particles)
        timer.start()
        self._live_refresh_timer = timer
        print(
            f'▶️ Replaying {len(self._live_playback_queue)} particle frames '
            f'at up to {_LIVE_PREVIEW_MAX_FPS} FPS.'
        )
        return 0

    def continue_to_run(self, refresh_interval: float = 10.0) -> int:
        """Run the retained simulation and play its particle snapshots at a capped rate."""
        simulation = self._pending_simulation
        runtime_config_path = self._pending_runtime_config_path
        self._pending_simulation = None
        self._pending_runtime_config_path = None
        if simulation is None:
            if runtime_config_path is not None:
                try:
                    runtime_config_path.unlink(missing_ok=True)
                except OSError:
                    pass
            print(
                "No particle-preview simulation is available. Run 'preview --with-particles' first.",
                file=sys.stderr,
            )
            return 1

        try:
            self._animation_run_completed = False
            simulation.buildSimulation()
            print('✅ Simulation built')
            simulation.initializeSimulation()
            print('✅ Simulation initialized')
            print("\n🚀 Running simulation...")
            self._start_live_refresh(refresh_interval)

            def run_simulation() -> None:
                try:
                    simulation.run()
                    self._animation_run_completed = True
                    print('✅ Simulation completed successfully!')
                except Exception as exc:
                    print(f'❌ Continued run failed: {exc}', file=sys.stderr)
                finally:
                    if runtime_config_path is not None:
                        try:
                            runtime_config_path.unlink(missing_ok=True)
                        except OSError:
                            pass

            self._live_run_thread = Thread(target=run_simulation, daemon=True)
            self._live_run_thread.start()
            print(
                f'ℹ️ Preview checks for new snapshots every {refresh_interval:g} seconds '
                f'and plays each batch at up to {_LIVE_PREVIEW_MAX_FPS} FPS.'
            )
            return 0
        except Exception as exc:
            print(f'❌ Continued run failed: {exc}', file=sys.stderr)
            return 1
        finally:
            if self._live_run_thread is None:
                del simulation
                if runtime_config_path is not None:
                    try:
                        runtime_config_path.unlink(missing_ok=True)
                    except OSError:
                        pass

    def _install_json_editor(
        self,
        config: SimulationConfig,
        *,
        config_path: Path,
        with_particles: bool,
    ) -> bool:
        """Create or rebind the save-only property editor for a preview config."""
        return install_json_editor(
            self,
            config,
            config_path=config_path,
            with_particles=with_particles,
        )

    def pump_ui_events(self) -> None:
        """Keep the persistent preview responsive while shell work blocks."""
        if self.plotter is None:
            return
        try:
            if self._using_background_plotter:
                app = getattr(self.plotter, 'app', None)
                if app is not None and hasattr(app, 'processEvents'):
                    app.processEvents()
                    return
                try:
                    from PySide6.QtWidgets import QApplication  # type: ignore[import]
                except Exception:
                    try:
                        from PyQt5.QtWidgets import QApplication  # type: ignore[import]
                    except Exception:
                        QApplication = None  # type: ignore[misc,assignment]
                if QApplication is not None:
                    app = QApplication.instance()
                    if app is not None:
                        app.processEvents()
                        return
            if hasattr(self.plotter, 'render'):
                self.plotter.render()
        except Exception:
            pass

    @staticmethod
    def _set_label_font_size(actor: Any, size: int) -> bool:
        try:
            mapper = actor.GetMapper()
            text_prop = mapper.GetLabelTextProperty()
            text_prop.SetFontSize(int(size))
            if hasattr(mapper, 'Modified'):
                mapper.Modified()
            if hasattr(actor, 'Modified'):
                actor.Modified()
            return True
        except Exception:
            return False

    def _rebuild_label_actor(self, entry: dict[str, Any], font_size: int) -> Any | None:
        if self.plotter is None:
            return entry.get('actor')

        actor = entry.get('actor')
        if actor is not None:
            try:
                self.plotter.remove_actor(actor, render=False)
            except TypeError:
                try:
                    self.plotter.remove_actor(actor)
                except Exception:
                    pass
            except Exception:
                pass

        points = entry.get('points')
        labels = entry.get('labels')
        text_color = entry.get('text_color', 'white')
        if not points or not labels:
            return actor

        try:
            new_actor = self.plotter.add_point_labels(
                points,
                labels,
                point_size=0,
                font_size=int(font_size),
                text_color=text_color,
                always_visible=True,
            )
        except Exception:
            return actor

        entry['actor'] = new_actor
        return new_actor

    def _remove_hover_observer(self) -> None:
        if self._hover_interactor is not None and self._hover_observer_tag is not None:
            try:
                self._hover_interactor.RemoveObserver(self._hover_observer_tag)
            except Exception:
                pass
        self._hover_interactor = None
        self._hover_observer_tag = None

    def _install_annotation_hover(self, visualizer: Any) -> None:
        self._remove_hover_observer()

        if self.plotter is None:
            return
        if not self._using_background_plotter:
            return

        entries = getattr(visualizer, 'annotation_label_actors', None) or []
        if not entries:
            return

        iren_wrapper = getattr(self.plotter, 'iren', None)
        interactor = getattr(iren_wrapper, 'interactor', None)
        renderer = getattr(self.plotter, 'renderer', None)
        if interactor is None or renderer is None:
            return

        try:
            import vtk  # type: ignore[import]
        except Exception:
            return

        spec_entries: list[dict[str, Any]] = []
        hover_coordinate = vtk.vtkCoordinate()
        hover_coordinate.SetCoordinateSystemToWorld()

        def _apply_size(spec_index: int, font_size: int) -> None:
            entry = spec_entries[spec_index]
            actor = entry.get('actor')
            if actor is not None and self._set_label_font_size(actor, font_size):
                return
            rebuilt_actor = self._rebuild_label_actor(entry, font_size)
            if rebuilt_actor is not None:
                entry['actor'] = rebuilt_actor

        for entry in entries:
            actor = entry.get('actor') if isinstance(entry, dict) else None
            if actor is None:
                continue
            base_size = int(entry.get('font_size', 8))
            hover_size = max(base_size + 4, 12)
            spec_index = len(spec_entries)
            entry['base_size'] = base_size
            entry['hover_size'] = hover_size
            spec_entries.append(entry)
            _apply_size(spec_index, base_size)

        if not spec_entries:
            return

        active_spec_index: int | None = None

        def _estimate_label_bounds(entry: dict[str, Any], dx: float, dy: float) -> tuple[float, float, float, float]:
            labels = entry.get('labels') or []
            text = str(labels[0]) if labels else ''
            lines = text.splitlines() or [text]
            max_chars = max((len(line) for line in lines), default=1)
            font_size = int(entry.get('base_size', 8))
            if active_spec_index is not None and spec_entries[active_spec_index] is entry:
                font_size = int(entry.get('hover_size', font_size))
            width = max(12.0, float(max_chars) * float(font_size) * 0.62 + 8.0)
            height = max(12.0, float(len(lines)) * float(font_size) * 1.35 + 4.0)
            pad = 6.0
            return (dx - pad, dy - pad, dx + width + pad, dy + height + pad)

        def _on_mouse_move(caller: Any, event: Any) -> None:
            nonlocal active_spec_index
            try:
                x, y = interactor.GetEventPosition()
            except Exception:
                return
            new_spec_index: int | None = None
            best_inside_score = float('inf')
            best_anchor_distance_sq = float(16 * 16)
            for idx, entry in enumerate(spec_entries):
                points = entry.get('points') or []
                if not points:
                    continue
                anchor = points[0]
                try:
                    if len(anchor) >= 3:
                        hover_coordinate.SetValue(float(anchor[0]), float(anchor[1]), float(anchor[2]))
                    else:
                        hover_coordinate.SetValue(float(anchor[0]), float(anchor[1]), 0.0)
                    dx, dy = hover_coordinate.GetComputedDisplayValue(renderer)
                except Exception:
                    continue

                left, bottom, right, top = _estimate_label_bounds(entry, float(dx), float(dy))
                if left <= float(x) <= right and bottom <= float(y) <= top:
                    center_x = 0.5 * (left + right)
                    center_y = 0.5 * (bottom + top)
                    inside_score = (center_x - float(x)) ** 2 + (center_y - float(y)) ** 2
                    if inside_score < best_inside_score:
                        best_inside_score = inside_score
                        new_spec_index = idx
                    continue

                if new_spec_index is not None:
                    continue
                distance_sq = (float(dx) - float(x)) ** 2 + (float(dy) - float(y)) ** 2
                if distance_sq <= best_anchor_distance_sq:
                    best_anchor_distance_sq = distance_sq
                    new_spec_index = idx

            if new_spec_index == active_spec_index:
                return

            if active_spec_index is not None:
                prev_entry = spec_entries[active_spec_index]
                _apply_size(active_spec_index, int(prev_entry.get('base_size', 8)))

            if new_spec_index is not None:
                next_entry = spec_entries[new_spec_index]
                _apply_size(new_spec_index, int(next_entry.get('hover_size', 12)))

            active_spec_index = new_spec_index

        try:
            tag = interactor.AddObserver('MouseMoveEvent', _on_mouse_move)
        except Exception:
            return

        self._hover_interactor = interactor
        self._hover_observer_tag = tag

    def _is_unchanged(
        self,
        config: SimulationConfig,
        *,
        resolved_config_path: Path,
        with_particles: bool,
    ) -> bool:
        if with_particles:
            return False
        payload = {
            'config': config.model_dump(exclude_none=True),
            'config_path': str(resolved_config_path.resolve()),
            'with_particles': with_particles,
        }
        signature = json.dumps(payload, sort_keys=True)
        unchanged = signature == self.last_signature
        self.last_signature = signature
        return unchanged

    def show_or_update(
        self,
        config: SimulationConfig,
        *,
        resolved_config_path: Path,
        with_particles: bool,
        force: bool = False,
    ) -> int:
        try:
            import pyvista as pv  # noqa: F401
        except ImportError:
            print(
                '❌ PyVista is not installed.\n'
                '   Install it with:  pip install sphinxsim[visualization]',
                file=sys.stderr,
            )
            return 1

        if self.plotter is not None and self._using_background_plotter:
            editor_refreshed = self._install_json_editor(
                config,
                config_path=resolved_config_path,
                with_particles=with_particles,
            )
            if not editor_refreshed:
                print('Preview canceled; unapplied editor changes were preserved.')
                return 0
        unchanged = self._is_unchanged(
            config,
            resolved_config_path=resolved_config_path,
            with_particles=with_particles,
        )
        if not force and unchanged:
            print('ℹ️ Preview unchanged; keeping existing window.')
            return 0

        from sphinxsim.visualization.preview import ConfigVisualizer

        visualizer = ConfigVisualizer(
            config,
            PROJECT_ROOT,
            config_path=resolved_config_path,
            off_screen=False,
        )

        ndim = visualizer._spatial_dim()
        vtp_dir: Path | None = None
        latest_particle_vtps: dict[str, Path] = {}
        vtp_dir = visualizer._try_build_geometries(ndim, with_particles=with_particles)
        if with_particles:
            latest_particle_vtps = visualizer._discover_latest_particle_vtps(vtp_dir)

        try:
            if sys.platform.startswith('linux'):
                os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
            import pyvista as pv
            pyvistaqt_error: Exception | None = None
            created_plotter = False
            try:
                from pyvistaqt import BackgroundPlotter  # type: ignore[import]
            except Exception as exc:
                BackgroundPlotter = None
                pyvistaqt_error = exc

            if self.plotter is None:
                created_plotter = True
                if BackgroundPlotter is not None:
                    self.plotter = BackgroundPlotter(
                        title='SPHinXsim - Configuration Preview',
                        show=True,
                        shape=(1, 1),
                        border=False,
                        window_size=(1400, 800),
                        update_app_icon=False,
                    )
                    self._using_background_plotter = True
                else:
                    self.plotter = pv.Plotter(
                        title='SPHinXsim - Configuration Preview',
                        off_screen=False,
                        shape=(1, 2),
                        border=False,
                        window_size=(1400, 800),
                    )
                    self._using_background_plotter = False
                    detail = f' ({pyvistaqt_error})' if pyvistaqt_error is not None else ''
                    print(
                        'ℹ️ pyvistaqt background mode is unavailable'
                        f'{detail}; persistent preview may become unresponsive while shell waits for input.\n'
                        '   Install dependencies with: pip install pyvistaqt PySide6\n'
                        '   (or use PyQt5 instead of PySide6)',
                        file=sys.stderr,
                    )

            if created_plotter and self._using_background_plotter:
                self._watch_preview_window_close()
                self._install_json_editor(
                    config,
                    config_path=resolved_config_path,
                    with_particles=with_particles,
                )

            self.plotter.clear()
            configure_layout = getattr(visualizer, '_configure_layout', None)
            if configure_layout is not None:
                configure_layout(self.plotter)
            draw_sidebar = getattr(visualizer, '_draw_preview_sidebar', None)
            if draw_sidebar is not None:
                draw_sidebar(self.plotter)
            visualizer._populate_plotter(self.plotter, vtp_dir, latest_particle_vtps)
            if with_particles:
                self._animation_run_completed = False
                pending_simulation = visualizer.take_particle_simulation()
                if pending_simulation is not None:
                    from sphinxsim.visualization.preview import _body_colour

                    (
                        self._pending_simulation,
                        self._pending_runtime_config_path,
                    ) = pending_simulation
                    self._live_output_dir = vtp_dir or (
                        PROJECT_ROOT / '.build-temp' / 'test_simulation' / 'output'
                    )
                    self._live_particle_body_names = visualizer._particle_generation_body_names()
                    self._live_particle_colours = {
                        body_name: _body_colour(body_name, config)
                        for body_name in self._live_particle_body_names
                    }
                    self._animation_output_dir = self._live_output_dir
                    self._animation_particle_body_names = set(self._live_particle_body_names)
                    self._animation_particle_colours = dict(self._live_particle_colours)
                    self._animation_config = config
                    self._animation_config_path = resolved_config_path
                else:
                    self._animation_output_dir = None
                    self._animation_particle_body_names = set()
                    self._animation_particle_colours = {}
                    self._animation_run_completed = False
            if configure_layout is not None:
                configure_layout(self.plotter)
            visualizer._configure_default_view(self.plotter, ndim)
            self.plotter.add_axes()
            self.plotter.show_grid(font_size=10, color=(0.25, 0.25, 0.25), bold=False)

            if vtp_dir:
                mode_label = 'VTP geometry'
            elif visualizer.used_cpp_bounds:
                mode_label = 'C++ bounds fallback'
            else:
                mode_label = 'No C++ geometry'
            dim_label = '2-D' if ndim == 2 else '3-D'
            sim_type_label = config.simulation_type.value.replace('_', ' ').title()
            config_info = f'{dim_label}  •  {sim_type_label}  •  {mode_label}'
            visualizer._add_config_info_text(self.plotter, config_info, ndim)
            self._install_annotation_hover(visualizer)

            if not self._using_background_plotter:
                try:
                    self.plotter.show(auto_close=False, interactive_update=True)
                except TypeError:
                    self.plotter.show()
            try:
                self.plotter.render()
            except Exception:
                pass
            return 0
        except ImportError as exc:
            print(f'❌ {exc}', file=sys.stderr)
            return 1
        except Exception as exc:
            print(f'❌ Preview failed: {exc}', file=sys.stderr)
            return 1


__all__ = ['_ShellPreviewRuntime']
