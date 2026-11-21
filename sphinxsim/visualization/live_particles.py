"""Particle updates shared by continued Preview runs and Agent observers."""

from sphinxsim.results import ResultCatalog, read_particle_mesh
from sphinxsim.visualization.preview import _PARTICLE_POINT_SIZE


def refresh_particle_actors(plotter, output_dir, body_colours, versions, catalog, visibility=None):
    frames = catalog.latest(output_dir, list(body_colours), include_generation=True)
    for body, frame in frames.items():
        version = (str(frame.path), frame.version)
        if versions.get(body) == version:
            continue
        try:
            mesh = read_particle_mesh(frame.path)
        except (OSError, ValueError, RuntimeError):
            continue
        actor = plotter.add_mesh(
            mesh, name=f"particle-preview-{body}", color=body_colours[body],
            opacity=0.95, style="points", point_size=_PARTICLE_POINT_SIZE,
            label=f"Particles: {body}", reset_camera=False,
        )
        if visibility is not None:
            visibility.add_actor(body, actor)
        versions[body] = version
    return frames
