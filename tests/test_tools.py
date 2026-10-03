import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from lanbridge import tools


def test_path_detection_does_not_download(tmp_path, monkeypatch):
    binary = tmp_path / "cloudflared.exe"
    binary.write_bytes(b"existing")
    monkeypatch.setattr(tools.shutil, "which", lambda _: str(binary))
    monkeypatch.setattr(tools, "version", lambda _: "cloudflared version test")
    assert tools.ensure_cloudflared()["source"] == "PATH"
    with pytest.raises(ValueError, match="指定路径不存在"):
        tools.ensure_cloudflared(str(tmp_path / "missing.exe"))


@pytest.mark.parametrize("valid", [True, False])
def test_download_verifies_before_execution_and_cleans_up(tmp_path, monkeypatch, valid):
    monkeypatch.setattr(tools, "TOOL_DIR", tmp_path / "bin")
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    monkeypatch.setattr(tools.platform, "machine", lambda: "AMD64")
    payload = b"MZ official mocked binary"
    verified = []
    monkeypatch.setattr(tools, "version", lambda path: verified.append(Path(path).read_bytes()) or "cloudflared version test")
    url = "https://github.com/cloudflare/cloudflared/releases/download/test/cloudflared-windows-amd64.exe"
    def respond(request):
        if str(request.url) == tools.RELEASE_API:
            return httpx.Response(200, json={"assets": [{"name": "cloudflared-windows-amd64.exe", "size": len(payload), "digest": "sha256:" + (hashlib.sha256(payload).hexdigest() if valid else "0" * 64), "browser_download_url": url}]})
        assert str(request.url) == url
        return httpx.Response(200, content=payload)
    original = httpx.Client
    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    if valid:
        result = tools.ensure_cloudflared()
        assert result["source"] == "download"
        assert Path(result["path"]).read_bytes() == payload
        assert verified == [payload]
    else:
        with pytest.raises(ValueError, match="完整性校验失败"):
            tools.ensure_cloudflared()
        assert verified == []
        assert list(tools.TOOL_DIR.iterdir()) == []


def test_mcp_stdio_handshake_and_discovery():
    root = Path(__file__).resolve().parents[1]
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    result = subprocess.run([sys.executable, str(root / "mcp_server.py")], input="\n".join(json.dumps(r) for r in requests) + "\n", text=True, capture_output=True, timeout=15, cwd=root)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == 2
    assert replies[0]["result"]["serverInfo"]["name"] == "LanBridge"
    names = {tool["name"] for tool in replies[1]["result"]["tools"]}
    assert {"lanbridge_prepare_connector", "lanbridge_preview", "lanbridge_apply"} <= names
    assert all("password" not in tool["inputSchema"].get("properties", {}) for tool in replies[1]["result"]["tools"])
