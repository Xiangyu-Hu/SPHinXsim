"""Small, optional renderers for AgentCAE result previews."""

from __future__ import annotations

from pathlib import Path


def render_vtp_snapshot(
    vtp_path: str | Path,
    image_path: str | Path,
    *,
    title: str = "SPHinXsim AgentCAE result",
) -> Path | None:
    """Render one VTP particle state to a headless PNG when PyVista is available.

    This function is deliberately presentation-only.  Scientific metrics are
    still obtained by the deterministic VTP analysis tools; the PNG is for
    human inspection in the Chatbot.
    """
    try:
        import pyvista as pv  # type: ignore[import]
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[import]
    except ImportError:
        return None

    source = Path(vtp_path).resolve()
    destination = Path(image_path).resolve()
    if not source.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        from sphinxsim.results import read_particle_mesh
        mesh = read_particle_mesh(source)
        point_count = int(getattr(mesh, "n_points", 0))
        if not point_count:
            return None
        points = mesh.points
        figure = plt.figure(figsize=(11, 7), dpi=120)
        if points.shape[1] >= 3 and float(points[:, 2].max() - points[:, 2].min()) > 1e-12:
            axes = figure.add_subplot(111, projection="3d")
            axes.scatter(points[:, 0], points[:, 1], points[:, 2], s=1, c="royalblue")
            axes.set_zlabel("z")
        else:
            axes = figure.add_subplot(111)
            axes.scatter(points[:, 0], points[:, 1], s=1, c="royalblue")
            axes.set_xlabel("x")
            axes.set_ylabel("y")
            axes.set_aspect("equal", adjustable="box")
        axes.set_title(title)
        figure.tight_layout()
        figure.savefig(destination)
        plt.close(figure)
    except Exception:
        # Visualization must never turn a successful simulation into a failed
        # Agent workflow.  The caller can still report the VTP path.
        return None
    return destination if destination.is_file() else None
