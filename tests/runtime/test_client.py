"""Real loopback transport boundary; this is not native rendering coverage."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from sphinxsim.desktop.client import request


def test_native_reference_uses_direct_authenticated_loopback_and_rejects_stale_instances(tmp_path, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, self.headers.get('Authorization'), body))
            raw = json.dumps({'ok': True, 'data': {'instance_id': 'current', 'version': 7}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    descriptor = tmp_path / 'desktop.json'
    reference = {'url': f'http://127.0.0.1:{server.server_port}/control', 'instance_id': 'current', 'token': 'owner-test-token'}
    descriptor.write_text(json.dumps(reference))
    for name in ('http_proxy', 'HTTP_PROXY'):
        monkeypatch.setenv(name, 'http://127.0.0.1:9')
    for name in ('no_proxy', 'NO_PROXY'):
        monkeypatch.setenv(name, '')
    try:
        assert request(descriptor, {'operation': 'status'}) == {'instance_id': 'current', 'version': 7}
        assert received == [('/control', 'Bearer owner-test-token', {'operation': 'status'})]
        with pytest.raises(ValueError, match='instance changed'):
            request(descriptor, {'operation': 'save', 'instance_id': 'previous'})
        descriptor.write_text(json.dumps({**reference, 'url': 'https://example.com/control'}))
        with pytest.raises(ValueError, match='loopback'):
            request(descriptor, {'operation': 'status'})
        assert len(received) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
