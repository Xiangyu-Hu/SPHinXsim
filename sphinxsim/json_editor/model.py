"""Shared JSON draft operations used by editor views and runtime hosts."""

from __future__ import annotations

import copy
import json
from typing import Any


_MISSING = object()


def value_at_path(payload: Any, path: tuple[Any, ...]) -> Any:
    value = payload
    for part in path:
        value = value[part]
    return value


def set_value_at_path(payload: Any, path: tuple[Any, ...], value: Any) -> None:
    if not path:
        raise ValueError("A field path cannot be empty.")
    value_at_path(payload, path[:-1])[path[-1]] = value


def delete_value_at_path(payload: Any, path: tuple[Any, ...]) -> None:
    if not path:
        raise ValueError("A field path cannot be empty.")
    del value_at_path(payload, path[:-1])[path[-1]]


def parse_field_value(original: Any, raw: Any) -> Any:
    if isinstance(original, bool):
        return bool(raw)
    if isinstance(original, int):
        return int(raw)
    if isinstance(original, float):
        return float(raw)
    if original is None:
        return json.loads(raw)
    if isinstance(original, (dict, list)):
        return json.loads(raw)
    return raw


def edit_list(
    payload: Any,
    path: tuple[Any, ...],
    operation: str,
    *,
    new_item: Any = _MISSING,
) -> bool:
    if operation == "add":
        target = value_at_path(payload, path)
        if not isinstance(target, list) or new_item is _MISSING:
            return False
        target.append(new_item)
        return True

    if not path or not isinstance(path[-1], int):
        return False
    target = value_at_path(payload, path[:-1])
    index = path[-1]
    if not isinstance(target, list) or not 0 <= index < len(target):
        return False

    if operation == "duplicate":
        target.insert(index + 1, copy.deepcopy(target[index]))
    elif operation == "remove":
        target.pop(index)
    elif operation == "up" and index > 0:
        target[index - 1], target[index] = target[index], target[index - 1]
    elif operation == "down" and index < len(target) - 1:
        target[index + 1], target[index] = target[index], target[index + 1]
    else:
        return False
    return True


def snapshot_payload(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def pop_undo_snapshot(history: list[str], fallback: str) -> str:
    return history.pop() if history else fallback


def validate_payload(payload: Any, config_type: type[Any]) -> Any:
    return config_type.model_validate(payload)
