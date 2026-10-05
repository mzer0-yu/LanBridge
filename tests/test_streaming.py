"""Exercise real Uvicorn framing and socket backpressure, not buffered ASGI clients."""
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import random
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from lanbridge.gateway import create_gateway, PASS_COOKIE, signed_pass
from test_security import service


@pytest.fixture
def streaming_gateway(service):
    payload = json.dumps({"preview": random.Random(42).randbytes(2653602).hex()}, separators=(",", ":")).encode()
    assert len(payload) == 5307218
    compressed = gzip.compress(payload)
    csv_body = b"index,x\n" + (b"1," + b"0" * 253 + b"\n") * 300000
    class Origin(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args): pass
        def do_GET(self):
            body = compressed if self.path == "/gzip" else csv_body if self.path == "/csv" else payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            if self.path == "/gzip": self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            try:
                if self.path == "/broken":
                    self.wfile.write(body[:1024 * 1024]); self.wfile.flush()
                    self.close_connection = True
                    return
                for offset in range(0, len(body), 16384):
                    self.wfile.write(body[offset:offset + 16384])
                    self.wfile.flush()
                    if self.path == "/slow-origin": time.sleep(.03)
            except (BrokenPipeError, ConnectionResetError): pass
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    source_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    source_thread.start()
    site = service.save_site({"name": "isolated protected test", "hostname": "app.example.com",
                              "origin": f"http://127.0.0.1:{origin.server_port}",
                              "human_check": True})
    listener = socket.socket(); listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(create_gateway(service), log_level="error", proxy_headers=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    for _ in range(150):
        if server.started: break
        time.sleep(.02)
    assert server.started
    headers = {"Host": "app.example.com", "CF-Visitor": '{"scheme":"https"}',
               "Cookie": PASS_COOKIE + "=" + signed_pass(service, site, "127.0.0.1")}
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}", headers, payload, compressed, csv_body
    finally:
        server.should_exit = True; thread.join(timeout=10)
        origin.shutdown(); origin.server_close(); source_thread.join(timeout=3)
        listener.close()


@pytest.mark.parametrize("path,slow", [("/json", False), ("/json", True), ("/gzip", True)])
def test_large_response_wire_bytes_and_slow_consumer(streaming_gateway, path, slow):
    url, headers, payload, compressed, _ = streaming_gateway
    expected = compressed if path == "/gzip" else payload
    with httpx.Client(headers=headers, trust_env=False, timeout=30) as client:
        with client.stream("GET", url + path) as response:
            assert response.status_code == 200
            wire = bytearray()
            for chunk in response.iter_raw(chunk_size=8192):
                wire.extend(chunk)
                if slow: time.sleep(.003)
            assert len(wire) == int(response.headers["content-length"]) == len(expected)
            assert hashlib.sha256(wire).digest() == hashlib.sha256(expected).digest()
            decoded = gzip.decompress(wire) if path == "/gzip" else wire
            assert json.loads(decoded) == json.loads(payload)
        head = client.head(url + "/json")
        assert head.status_code == 200 and not head.content
        assert int(head.headers["content-length"]) == len(payload)


def test_upstream_disconnect_is_correlated_to_its_request(streaming_gateway, service):
    url, headers, _, _, _ = streaming_gateway
    with httpx.Client(headers=headers, trust_env=False, timeout=30) as client:
        response = client.get(url + "/broken")
        assert response.status_code == 502
        assert len(response.content) == int(response.headers["content-length"])
    for _ in range(50):
        text = (service.store.root / "gateway.log").read_text(encoding="utf-8") if (service.store.root / "gateway.log").exists() else ""
        if '"path": "/broken"' in text: break
        time.sleep(.02)
    assert '"path": "/broken"' in text
    assert '"upstream_bytes": 1048576' in text
    assert '"upstream_error": "RemoteProtocolError"' in text
    assert 'Too little data for declared Content-Length' not in text


def test_full_csv_with_slow_consumer(streaming_gateway):
    url, headers, _, _, expected = streaming_gateway
    checksum = hashlib.sha256()
    size = lines = 0
    with httpx.Client(headers=headers, trust_env=False, timeout=30) as client:
        with client.stream("GET", url + "/csv") as response:
            for chunk in response.iter_raw(chunk_size=65536):
                size += len(chunk); lines += chunk.count(b"\n"); checksum.update(chunk)
                time.sleep(.001)
            assert size == len(expected) == int(response.headers["content-length"])
    assert checksum.digest() == hashlib.sha256(expected).digest()
    assert lines == 300001


def test_stream_resources_close_on_failure_and_consumer_cancel(streaming_gateway, monkeypatch):
    import lanbridge.gateway as gateway
    url, headers, _, _, _ = streaming_gateway
    clients, spools = [], []
    real_client, real_spool = httpx.AsyncClient, gateway.tempfile.SpooledTemporaryFile
    class TrackingClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs); clients.append(self)
    def tracked_spool(*args, **kwargs):
        value = real_spool(*args, **kwargs); spools.append(value); return value
    monkeypatch.setattr(gateway.httpx, "AsyncClient", TrackingClient)
    monkeypatch.setattr(gateway.tempfile, "SpooledTemporaryFile", tracked_spool)
    with httpx.Client(headers=headers, trust_env=False, timeout=30) as client:
        assert client.get(url + "/broken").status_code == 502
        with client.stream("GET", url + "/csv") as response:
            assert response.status_code == 200
            next(response.iter_raw())
            # Close the consumer before the complete, prevalidated file is sent.
    # Cancel before response headers arrive while a slow source is still sending.
    connection = socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])))
    connection.sendall(("GET /slow-origin HTTP/1.1\r\n" + "\r\n".join(f"{k}: {v}" for k, v in headers.items()) + "\r\n\r\n").encode())
    for _ in range(100):
        if len(spools) == 3: break
        time.sleep(.01)
    connection.close()
    for _ in range(100):
        if all(value.closed for value in spools): break
        time.sleep(.02)
    assert len(clients) == len(spools) == 3
    assert all(value.is_closed for value in clients)
    assert all(value.closed for value in spools)
