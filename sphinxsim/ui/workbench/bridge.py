"""Authenticated loopback connection between the desktop and its Chainlit child."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import secrets
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class WorkbenchBridge:
    def __init__(self, document):
        self.document = document
        self.token = secrets.token_urlsafe(32)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.dispatch()

            def do_POST(self):
                self.dispatch()

            def dispatch(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + bridge.token):
                    self.reply(401, {"error": "Unauthorized"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 0 or size > 2_000_000:
                        raise ValueError("Request is too large")
                    data = json.loads(self.rfile.read(size)) if size else {}
                    if self.command == "GET" and self.path == "/state":
                        result = bridge.document.snapshot()
                    elif self.command == "POST" and self.path == "/proposal":
                        result = bridge.document.propose(data["revision"], data["payload"], data["instruction"])
                    elif self.command == "POST" and self.path == "/observe":
                        from sphinxsim.visualization.agent_preview import WorkflowPreviewSource
                        WorkflowPreviewSource(".", data["workflow_id"])
                        bridge.document.actions.put(("observe", data["workflow_id"]))
                        result = {"accepted": True}
                    else:
                        self.reply(404, {"error": "Unknown endpoint"})
                        return
                    self.reply(200, result)
                except (ValueError, KeyError, TypeError) as exc:
                    self.reply(409, {"error": str(exc)})

            def reply(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class WorkbenchClient:
    def __init__(self, url, token):
        self.url, self.token = url, token

    @classmethod
    def from_environment(cls):
        url = os.environ.get("SPHINXSIM_WORKBENCH_URL")
        return cls(url, os.environ["SPHINXSIM_WORKBENCH_TOKEN"]) if url else None

    def request(self, path, payload=None):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ValueError(json.load(exc).get("error", str(exc))) from exc

    def state(self):
        return self.request("/state")

    def observe(self, workflow_id):
        return self.request("/observe", {"workflow_id": workflow_id})

    def propose_update(self, llm, instruction):
        from sphinxsim.config.schemas import SimulationConfig
        from sphinxsim.config.update_patch import apply_update_patch
        snapshot = self.state()
        config = SimulationConfig.model_validate_json(snapshot["text"])
        patch = llm.update_patch(config, instruction, strict=True)
        result = apply_update_patch(json.loads(snapshot["text"]), patch, strict=True)
        if result.errors:
            raise ValueError("; ".join(result.errors))
        return self.request("/proposal", dict(revision=snapshot["revision"], payload=result.updated,
                                              instruction=instruction))
