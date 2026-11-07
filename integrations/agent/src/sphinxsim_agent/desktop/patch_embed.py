"""Reproducible, fail-closed insertion into the pinned KasmVNC 1.5.0 assets.

The frame hook runs AFTER the async decoded rectangle queue is rendered (v > 0),
not at transport connect/flip receipt. Retain the original framebuffer on drops.
Standalone KasmVNC keeps its original behavior. Third-party license files remain.
"""
import hashlib
from pathlib import Path
import sys


def patch(root):
    root = Path(root)
    bundle = root / "assets/ui-BOjwDkC7.js"
    data = bundle.read_bytes()
    if hashlib.sha256(data).hexdigest() != "351b7a0b3b05261b8bff17419e28a8216f691e2eee1e6800842f4773a143c727":
        raise ValueError("Unexpected KasmVNC bundle: inspect the new render pipeline before updating this patch")
    source = data.decode()
    changes = {
        'this._flipCnt+=1,this._flushing': '(v>0&&window.__sphinxsimEmbedded?.painted(this._rfb)),this._flipCnt+=1,this._flushing',
        'o.rfb.addEventListener("connect",o.connectFinished)': 'window.__sphinxsimEmbedded?.attach(o.rfb,o),o.rfb.addEventListener("connect",o.connectFinished)',
        'this._target.removeChild(this._screen)': 'window.__sphinxsimEmbedded?.retain(this)||this._target.removeChild(this._screen)',
        'disconnectFinished(n){const e=o.connected;': 'disconnectFinished(n){if(window.__sphinxsimEmbedded){o.connected=!1;o.rfb=void 0;return}const e=o.connected;',
        'receiveMessage(n){var e,t,r;': 'receiveMessage(n){if(window.__sphinxsimEmbedded)return;var e,t,r;',
    }
    for original, replacement in changes.items():
        if source.count(original) != 1:
            raise ValueError("KasmVNC insertion anchor changed: " + original)
        source = source.replace(original, replacement)
    bundle.write_text(source)
    index = root / "index.html"
    html = index.read_text()
    if html.count("</head>") != 1:
        raise ValueError("Unexpected KasmVNC entry")
    # Classic bridge runs before the module client, and only activates with the explicit embed query.
    index.write_text(html.replace("</head>", '<script src="./assets/sphinxsim-embed-policy.js"></script><link rel="stylesheet" href="./assets/sphinxsim-embed.css"><script src="./assets/sphinxsim-embed.js"></script></head>'))


if __name__ == "__main__":
    patch(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/kasmvnc/www")
