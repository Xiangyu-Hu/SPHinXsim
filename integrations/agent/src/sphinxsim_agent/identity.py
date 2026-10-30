"""Versioned effective-configuration identity; not RFC 8785 / JCS.

Legacy configurationHash fields keep their historical encoding. Consumers must
use the explicitly named/versioned identity below for cross-report comparison.
"""
import hashlib
import json


def effective_identity(configuration):
    raw = json.dumps(configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    return {"version": 1, "scope": "effective-native-configuration", "encoding": "python-json-sorted-ascii-compact-v1",
            "algorithm": "sha256", "sha256": hashlib.sha256(raw).hexdigest()}
