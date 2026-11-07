"""Private loopback RPC for an explicitly enabled native desktop, not MCP."""
import json
import sys
import urllib.request
import urllib.error


def request(body):
    payload = json.dumps(body, allow_nan=False).encode()
    if len(payload) > 1 << 20:
        raise ValueError("Request too large")
    request = urllib.request.Request("http://127.0.0.1:6902/control", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read((48 << 20) + 1)
    if len(raw) > 48 << 20:
        raise ValueError("Desktop response exceeds limit")
    value = json.loads(raw)
    if not value.get("ok"):
        raise ValueError(value.get("error", "Desktop operation failed"))
    return value.get("data")


def main():
    try:
        raw = sys.stdin.buffer.readline((1 << 20) + 1)
        if len(raw) > 1 << 20:
            raise ValueError("Request too large")
        print(json.dumps({"ok": True, "data": request(json.loads(raw))}))
    except urllib.error.URLError:
        # Preserve the host's existing initializing/unavailable projection when
        # Qt has not opened its control socket yet. Not an input conflict.
        print(json.dumps({"ok": False}))
    except Exception as exc:
        # Worker expects an envelope even on a transport error.
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}))


if __name__ == "__main__":
    main()
