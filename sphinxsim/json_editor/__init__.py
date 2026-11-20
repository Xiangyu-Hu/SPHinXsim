"""JSON configuration editor and formatting utilities."""

from sphinxsim.json_editor.editor import (
    JsonEditorMixin,
    _apply_payload_diff,
    _config_spatial_dim,
    _new_json_editor_list_item,
)
from sphinxsim.json_editor.json_format import (
    RawFloat,
    find_prettier_options,
    format_json,
    format_json_like_source,
    load_json_preserving_floats,
    object_layout,
)

__all__ = [
    "JsonEditorMixin",
    "JsonPropertyEditor",
    "RawFloat",
    "find_prettier_options",
    "format_json",
    "format_json_like_source",
    "load_json_preserving_floats",
    "object_layout",
]


def __getattr__(name: str):
    if name == "JsonPropertyEditor":
        from sphinxsim.json_editor.widget import JsonPropertyEditor

        return JsonPropertyEditor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
