"""Migrate older authoring documents into the current native schema."""
from copy import deepcopy


def migrate_legacy_configuration(payload):
    """Return an editable copy, leaving the caller's saved document intact."""
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be a JSON object")
    payload = deepcopy(payload)
    generation = payload.get("particle_generation")
    if isinstance(generation, dict) and {"settings", "build_and_run"} & generation.keys():
        if set(generation) != {"settings", "build_and_run"}:
            raise ValueError("particle_generation mixes legacy settings with current or unknown fields")
        if generation["build_and_run"] is not True:
            raise ValueError("Disabled legacy particle generation requires an explicit configuration update")
        if not isinstance(generation["settings"], dict):
            raise ValueError("Legacy particle_generation.settings must be an object")
        payload["particle_generation"] = generation["settings"]

    for shape in payload.get("geometries", {}).get("shapes", []):
        if shape.get("type") != "multipolygon":
            continue
        for polygon in shape.get("polygons") or []:
            if polygon.get("type") != "container_box":
                continue
            legacy_keys = ("inner_lower_bound", "inner_upper_bound")
            if not any(key in polygon for key in legacy_keys):
                continue
            if polygon.get("inner_box") is not None:
                raise ValueError("container_box contains both inner_box and legacy inner bounds")
            if not all(key in polygon for key in legacy_keys):
                raise ValueError("Legacy container_box requires inner_lower_bound and inner_upper_bound")
            polygon["inner_box"] = {
                "lower_bound": polygon.pop("inner_lower_bound"),
                "upper_bound": polygon.pop("inner_upper_bound"),
            }
    return payload
