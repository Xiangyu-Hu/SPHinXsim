"""Prettier-style JSON formatting that preserves the layout of an existing file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Layout = dict[tuple, bool]
_BLANK = "\0blank"


class RawFloat(float):
    """A float that remembers its source text (e.g. ``1.0e-2``) for faithful output."""

    raw: str

    def __new__(cls, text: str) -> "RawFloat":
        obj = super().__new__(cls, text)
        obj.raw = text
        return obj


def load_json_preserving_floats(text: str) -> Any:
    return json.loads(text, parse_float=RawFloat)


def find_prettier_options(start: Path) -> dict[str, Any] | None:
    """Return the JSON ``.prettierrc`` options found above *start*, if any."""
    try:
        directory = start.resolve().parent
    except OSError:
        return None
    for folder in (directory, *directory.parents):
        candidate = folder / ".prettierrc"
        if candidate.is_file():
            try:
                options = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return options if isinstance(options, dict) else None
    return None


def object_layout(text: str) -> Layout:
    """Map each object path to whether its first key started on a new line."""
    layout: Layout = {}
    length = len(text)
    pos = 0

    def skip_ws() -> None:
        nonlocal pos
        while pos < length and text[pos] in " \t\r\n":
            pos += 1

    def read_string() -> str:
        nonlocal pos
        start = pos
        pos += 1
        while text[pos] != '"':
            pos += 2 if text[pos] == "\\" else 1
        pos += 1
        return json.loads(text[start:pos])

    def read_value(path: tuple) -> None:
        nonlocal pos
        char = text[pos]
        if char == "{":
            pos += 1
            start = pos
            skip_ws()
            if text[pos] == "}":
                pos += 1
                return
            layout[path] = "\n" in text[start:pos]
            first = True
            while True:
                gap_start = pos
                skip_ws()
                key = read_string()
                if not first and text.count("\n", gap_start, pos) >= 2:
                    layout[(_BLANK, *path, key)] = True
                first = False
                skip_ws()
                pos += 1  # ':'
                skip_ws()
                read_value((*path, key))
                skip_ws()
                separator = text[pos]
                pos += 1
                if separator == "}":
                    return
        elif char == "[":
            pos += 1
            skip_ws()
            if text[pos] == "]":
                pos += 1
                return
            index = 0
            while True:
                if index and text.count("\n", gap_start, pos) >= 2:
                    layout[(_BLANK, *path, index)] = True
                read_value((*path, index))
                index += 1
                skip_ws()
                separator = text[pos]
                pos += 1
                if separator == "]":
                    return
                gap_start = pos
                skip_ws()
        elif char == '"':
            read_string()
        else:
            while pos < length and text[pos] not in ",]} \t\r\n":
                pos += 1

    try:
        skip_ws()
        read_value(())
    except (IndexError, ValueError):
        return {}
    return layout


def _scalar(value: Any) -> str:
    if isinstance(value, RawFloat):
        return value.raw
    return json.dumps(value, ensure_ascii=False)


def _can_be_flat(value: Any, path: tuple, layout: Layout) -> bool:
    if isinstance(value, dict):
        if not value:
            return True
        if layout.get(path, True):
            return False
        return all(_can_be_flat(v, (*path, k), layout) for k, v in value.items())
    if isinstance(value, list):
        return all(_can_be_flat(v, (*path, i), layout) for i, v in enumerate(value))
    return True


def _flat(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{ " + ", ".join(f"{_scalar(k)}: {_flat(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_flat(v) for v in value) + "]"
    return _scalar(value)


def _must_break_list(value: list) -> bool:
    if len(value) <= 1:
        return False
    first = value[0]
    if isinstance(first, dict):
        return all(isinstance(v, dict) and len(v) > 1 for v in value)
    if isinstance(first, list):
        return all(isinstance(v, list) and len(v) > 1 for v in value)
    return False


def format_json(
    value: Any,
    *,
    layout: Layout | None = None,
    print_width: int = 80,
    tab_width: int = 2,
) -> str:
    """Format *value* like Prettier, keeping inline objects that were inline before."""
    layout = layout or {}

    def join(entries: list[str], paths: list[tuple]) -> str:
        out = entries[0]
        for entry, member_path in zip(entries[1:], paths[1:]):
            blank = layout.get((_BLANK, *member_path), False)
            out += (",\n\n" if blank else ",\n") + entry
        return out

    def fmt(node: Any, path: tuple, depth: int, used: int) -> str:
        """Format *node*; *used* is the column consumed on its first line, incl. trailing comma."""
        pad = " " * (tab_width * depth)
        inner = " " * (tab_width * (depth + 1))
        if isinstance(node, dict):
            if not node:
                return "{}"
            if _can_be_flat(node, path, layout):
                flat = _flat(node)
                if used + len(flat) <= print_width:
                    return flat
            lines = []
            for key, child in node.items():
                prefix = f"{_scalar(key)}: "
                body = fmt(child, (*path, key), depth + 1, len(inner) + len(prefix) + 1)
                lines.append(f"{inner}{prefix}{body}")
            return "{\n" + join(lines, [(*path, k) for k in node]) + f"\n{pad}}}"
        if isinstance(node, list):
            if not node:
                return "[]"
            if not _must_break_list(node) and _can_be_flat(node, path, layout):
                flat = _flat(node)
                if used + len(flat) <= print_width:
                    return flat
                if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in node):
                    rows: list[str] = []
                    current = ""
                    for index, item in enumerate(node):
                        text = _scalar(item) + ("," if index < len(node) - 1 else "")
                        if current and len(inner) + len(current) + 1 + len(text) > print_width:
                            rows.append(current)
                            current = text
                        else:
                            current = f"{current} {text}" if current else text
                    rows.append(current)
                    return "[\n" + "\n".join(f"{inner}{row}" for row in rows) + f"\n{pad}]"
            items = [
                f"{inner}{fmt(child, (*path, index), depth + 1, len(inner) + 1)}"
                for index, child in enumerate(node)
            ]
            return "[\n" + join(items, [(*path, i) for i in range(len(node))]) + f"\n{pad}]"
        return _scalar(node)

    return fmt(value, (), 0, 0)
