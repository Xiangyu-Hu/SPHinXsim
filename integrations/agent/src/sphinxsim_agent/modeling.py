"""Fixed, data-only CAD interpreter. Never evaluates Python or expressions."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

VERSION = 1
OPERATIONS = ("box", "cylinder", "sphere", "cone", "extrude", "revolve", "loft", "sweep",
              "union", "subtract", "intersect", "translate", "rotate", "scale", "fillet", "chamfer")
PARAMETERS = {
    "box": ("size",), "cylinder": ("radius", "height"), "sphere": ("radius",), "cone": ("radius1", "radius2", "height"),
    "extrude": ("profile", "distance"), "revolve": ("profile", "angle", "axisStart", "axisEnd"),
    "loft": ("profiles", "ruled"), "sweep": ("profile", "path"),
    "union": ("inputs",), "subtract": ("inputs",), "intersect": ("inputs",),
    "translate": ("input", "offset"), "rotate": ("input", "axisStart", "axisEnd", "angle"),
    "scale": ("input", "factor"), "fillet": ("input", "radius", "edges"), "chamfer": ("input", "distance", "edges"),
}


def recipe_contract():
    return {"version": VERSION, "units": ["m", "cm", "mm"], "maxOperations": 256,
        "operations": {name: {"parameters": fields} for name, fields in PARAMETERS.items()},
        "values": {
            "size": "three positive lengths; box centered at origin", "radius": "positive length",
            "height": "positive length along +Z from zero", "radius1": "nonnegative cone bottom radius", "radius2": "nonnegative cone top radius",
            "input": "ID of preceding solid", "inputs": "2–64 preceding solid IDs",
            "offset": "three lengths", "axisStart": "three coordinates (two sketch-local coordinates for revolve)",
            "axisEnd": "three coordinates (two sketch-local coordinates for revolve)", "angle": "degrees; revolve defaults to 360",
            "factor": "positive dimensionless scale", "edges": "all | parallel_x | parallel_y | parallel_z",
            "distance": "positive length", "ruled": "optional boolean; defaults false",
            "profiles": "2–64 closed profiles", "path": "2–256 three-coordinate spline points",
            "profile": {"plane": "XY | XZ | YZ (default XY)", "origin": "optional three lengths",
                "circle": "positive radius, exclusive with segments",
                "segments": "3–2048 entries: first {move:[x,y]}, then {line:[x,y]}, {arc:[x,y],through:[x,y]}, or {spline:[[x,y],...]}; closed automatically"}},
        "output": "ID of final solid", "tolerance": "absolute meters, default .0005, minimum .000001",
        "angularTolerance": "radians, .01–1, default .1",
        "example": {"version": 1, "unit": "m", "operations": [{"id": "body", "op": "box", "args": {"size": [1, .5, .2]}}], "output": "body"}}


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e9 or positive and value <= 0:
        raise ValueError("Expected a finite numeric parameter" + (" greater than zero" if positive else ""))
    return float(value)


def vector(value, length=3):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"Expected {length} coordinates")
    return tuple(number(n) for n in value)


def sketch(cq, spec):
    """Closed profiles; optional workplane translation is baked into the CAD solid."""
    if not isinstance(spec, dict) or set(spec) - {"plane", "origin", "segments", "circle"}:
        raise ValueError("Invalid sketch fields")
    plane = spec.get("plane", "XY")
    if plane not in ("XY", "XZ", "YZ"):
        raise ValueError("Use XY, XZ or YZ sketch plane")
    work = cq.Workplane(plane, origin=vector(spec.get("origin", [0, 0, 0])))
    if "circle" in spec:
        if "segments" in spec:
            raise ValueError("Use either circle or segments")
        return work.circle(number(spec["circle"], positive=True))
    segments = spec.get("segments")
    if not isinstance(segments, list) or not 3 <= len(segments) <= 2048:
        raise ValueError("A profile needs 3–2048 segments")
    first = segments[0]
    if set(first) != {"move"}:
        raise ValueError("Start sketch with move")
    work = work.moveTo(*vector(first["move"], 2))
    for item in segments[1:]:
        if set(item) == {"line"}:
            work = work.lineTo(*vector(item["line"], 2))
        elif set(item) == {"arc", "through"}:
            work = work.threePointArc(vector(item["through"], 2), vector(item["arc"], 2))
        elif set(item) == {"spline"} and isinstance(item["spline"], list) and 2 <= len(item["spline"]) <= 256:
            work = work.spline([vector(p, 2) for p in item["spline"]], includeCurrent=True)
        else:
            raise ValueError("Sketch accepts line, three-point arc or spline segments")
    return work.close()


def build(recipe, directory):
    if not isinstance(recipe, dict) or set(recipe) - {"version", "unit", "operations", "output", "tolerance", "angularTolerance"}:
        raise ValueError("Invalid CAD recipe fields")
    if recipe.get("version") != VERSION or recipe.get("unit", "m") not in ("m", "mm", "cm"):
        raise ValueError("Use recipe version 1 and unit m, cm or mm")
    steps = recipe.get("operations")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 256:
        raise ValueError("Provide 1–256 CAD operations")
    import cadquery as cq
    objects = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {"id", "op", "args"}:
            raise ValueError(f"Operation {index}: use id, op and args")
        name, op, args = step["id"], step["op"], step["args"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or name in objects:
            raise ValueError(f"Operation {index}: invalid or duplicate id")
        if op not in OPERATIONS or not isinstance(args, dict):
            raise ValueError(f"{name}: unsupported operation")
        def inputs(key="inputs"):
            refs = args.get(key)
            if not isinstance(refs, list) or not 2 <= len(refs) <= 64 or any(ref not in objects for ref in refs):
                raise ValueError("Reference 2–64 preceding solids")
            return [objects[ref] for ref in refs]
        def source():
            if args.get("input") not in objects:
                raise ValueError("Input must reference a preceding solid")
            return objects[args["input"]]
        if set(args) - set(PARAMETERS[op]):
            raise ValueError(f"{name}: unknown parameters: {sorted(set(args) - set(PARAMETERS[op]))}")
        try:
            work = cq.Workplane("XY")
            if op == "box":
                size = vector(args["size"])
                if min(size) <= 0:
                    raise ValueError("Box dimensions must be positive")
                result = work.box(*size)
            elif op == "cylinder":
                result = work.circle(number(args["radius"], positive=True)).extrude(number(args["height"], positive=True))
            elif op == "sphere":
                result = work.sphere(number(args["radius"], positive=True))
            elif op == "cone":
                r1, r2 = number(args["radius1"]), number(args["radius2"])
                if min(r1, r2) < 0 or max(r1, r2) <= 0:
                    raise ValueError("Invalid cone radii")
                result = cq.Workplane(obj=cq.Solid.makeCone(r1, r2, number(args["height"], positive=True)))
            elif op == "extrude":
                result = sketch(cq, args["profile"]).extrude(number(args["distance"], positive=True))
            elif op == "revolve":
                result = sketch(cq, args["profile"]).revolve(number(args.get("angle", 360)),
                    vector(args.get("axisStart", [0, 0]), 2), vector(args.get("axisEnd", [0, 1]), 2))
            elif op == "loft":
                profiles = args["profiles"]
                if not isinstance(profiles, list) or not 2 <= len(profiles) <= 64 or type(args.get("ruled", False)) is not bool:
                    raise ValueError("Loft needs 2–64 profiles and boolean ruled")
                wires = [wire for p in profiles for wire in sketch(cq, p).val().Wires()]
                result = cq.Workplane(obj=cq.Solid.makeLoft(wires, ruled=args.get("ruled", False)))
            elif op == "sweep":
                points = args["path"]
                if not isinstance(points, list) or not 2 <= len(points) <= 256:
                    raise ValueError("Sweep path needs 2–256 3D points")
                path = cq.Wire.assembleEdges([cq.Edge.makeSpline([cq.Vector(*vector(p)) for p in points])])
                result = sketch(cq, args["profile"]).sweep(cq.Workplane(obj=path))
            elif op in ("union", "subtract", "intersect"):
                solids = inputs()
                result = solids[0]
                for solid in solids[1:]:
                    result = {"union": result.union, "subtract": result.cut, "intersect": result.intersect}[op](solid)
            elif op == "translate":
                result = source().translate(vector(args["offset"]))
            elif op == "rotate":
                result = source().rotate(vector(args["axisStart"]), vector(args["axisEnd"]), number(args["angle"]))
            elif op == "scale":
                result = cq.Workplane(obj=source().val().scale(number(args["factor"], positive=True)))
            else:
                selector = args.get("edges")
                if selector not in ("all", "parallel_x", "parallel_y", "parallel_z"):
                    raise ValueError("Edges must be all or parallel_x/y/z")
                edges = source().edges() if selector == "all" else source().edges("|" + selector[-1].upper())
                result = edges.fillet(number(args["radius"], positive=True)) if op == "fillet" else edges.chamfer(number(args["distance"], positive=True))
            if not result.val().isValid() or not result.val().Solids() or result.val().Volume() <= 0:
                raise ValueError("Operation produced an empty or invalid solid")
            objects[name] = result
        except Exception as exc:
            raise ValueError(f"CAD operation {name} ({op}) failed: {str(exc)[:240]}") from exc
    if recipe.get("output") not in objects:
        raise ValueError("Output must reference an existing solid")
    factor = {"m": 1, "cm": .01, "mm": .001}[recipe.get("unit", "m")]
    solid = objects[recipe["output"]].val().scale(factor)
    tolerance = number(recipe.get("tolerance", .0005), positive=True)
    angle = number(recipe.get("angularTolerance", .1), positive=True)
    if tolerance < 1e-6 or not .01 <= angle <= 1:
        raise ValueError("STL tolerance must be at least 1e-6 m and angularTolerance between .01 and 1 rad")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(solid, str(directory / "model.step"))
    cq.exporters.export(solid, str(directory / "model.stl"), tolerance=tolerance, angularTolerance=angle,
                        opt={"ascii": False, "relative": False})
    if any(p.stat().st_size > 512 << 20 for p in directory.iterdir() if p.is_file()):
        raise ValueError("Generated asset exceeds 512 MiB")
    from sphinxsim_agent.project_input import validate_stl
    validate_stl(directory / "model.stl")
    (directory / "recipe.json").write_text(json.dumps(recipe, allow_nan=False))
    bounds = solid.BoundingBox()
    return {"version": VERSION, "meshChecks": "closed_manifold_finite_nondegenerate", "cadqueryVersion": cq.__version__, "units": "m", "volume": solid.Volume(),
            "bounds": [[bounds.xmin, bounds.ymin, bounds.zmin], [bounds.xmax, bounds.ymax, bounds.zmax]],
            "files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": file_hash(p)}
                      for p in directory.iterdir() if p.name in ("model.stl", "model.step", "recipe.json")]}


def main():
    parser = argparse.ArgumentParser(description="Generate CAD solids from bounded JSON operations")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        raw = Path(args.input).read_bytes()
        if len(raw) > 1 << 20:
            raise ValueError("CAD recipe exceeds 1 MiB")
        result = build(json.loads(raw), output)
        (output / "cad-result.json").write_text(json.dumps({"ok": True, **result}, allow_nan=False))
    except Exception as exc:
        output.mkdir(parents=True, exist_ok=True)
        (output / "cad-result.json").write_text(json.dumps({"ok": False, "error": str(exc)[:1000]}))
        sys.exit(1)


if __name__ == "__main__":
    main()
