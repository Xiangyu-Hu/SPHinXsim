"""Particle updates shared by continued Preview runs and Agent observers."""

from sphinxsim.results import ResultCatalog, read_particle_mesh
from sphinxsim.visualization.preview import (
    _PARTICLE_DEFAULT_OPACITY,
    _PARTICLE_POINT_SIZE,
    _particle_label,
)


def add_or_update_particle_actor(plotter, body, mesh, colour, *, label=None):
    name = f"particle-preview-{body}"
    renderer = getattr(plotter, "renderer", None)
    actors = getattr(renderer, "actors", None)
    actor = actors.get(name) if isinstance(actors, dict) else None
    if actor is None:
        return plotter.add_mesh(
            mesh,
            name=name,
            color=colour,
            opacity=_PARTICLE_DEFAULT_OPACITY,
            style="points",
            point_size=_PARTICLE_POINT_SIZE,
            label=label or _particle_label(body),
            reset_camera=False,
        )

    mapper = actor.GetMapper()
    mapper.SetInputData(mesh)
    mapper.Modified()
    actor.Modified()
    return actor


def refresh_particle_actors(plotter, output_dir, body_colours, versions, catalog, visibility=None,
                            time_display=None):
    frames = catalog.latest(output_dir, list(body_colours), include_generation=True)
    for body, frame in frames.items():
        version = (str(frame.path), frame.version)
        if versions.get(body) == version:
            continue
        try:
            mesh = read_particle_mesh(frame.path)
        except (OSError, ValueError, RuntimeError):
            continue
        actor = add_or_update_particle_actor(
            plotter, body, mesh, body_colours[body], label=f"Particles: {body}"
        )
        if visibility is not None:
            visibility.add_actor(body, actor, mesh, initialized=not frame.generation)
        if time_display is not None:
            time_display.update(body, mesh, generation=frame.generation)
        versions[body] = version
    return frames
