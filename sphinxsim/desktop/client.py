"""Owner-provided, authenticated loopback connection to a native application."""
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


def request(reference_file, command):
    reference = json.loads(Path(reference_file).read_text())
    url = urlparse(reference["url"])
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password or url.path != "/control":
        raise ValueError("Native desktop reference must use the owner's loopback endpoint")
    if command.get("operation") != "status" and command.get("instance_id") != reference["instance_id"]:
        raise ValueError("Desktop instance changed; observe again")
    raw = json.dumps(command, allow_nan=False).encode()
    if len(raw) > 1 << 20:
        raise ValueError("Desktop input exceeds 1 MiB")
    query = Request(reference["url"], data=raw, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + reference["token"]})
    # A loopback credential must never be sent through an environment proxy.
    with build_opener(ProxyHandler({})).open(query, timeout=25) as response:
        data = response.read((48 << 20) + 1)
    if len(data) > 48 << 20:
        raise ValueError("Desktop response exceeds 48 MiB")
    result = json.loads(data)
    if not result.get("ok"):
        raise ValueError(result.get("error", "Native operation failed"))
    return result["data"]
