"""LanBridge stdio MCP bridge, using the authenticated loopback admin API."""
import json
import os
import sys
from urllib.parse import urlsplit

import httpx
from lanbridge.models import Site

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
SITE_SCHEMA = Site.model_json_schema()
# Visitor passwords and Cloudflare secrets are entered through the local UI.
SITE_SCHEMA["additionalProperties"] = False
ROUTES = {
    "lanbridge_status": ("GET", "state", EMPTY, "查看配置缺项、网站和连接器状态", True),
    "lanbridge_save_site": ("POST", "sites", SITE_SCHEMA, "保存网站与访问策略；缺少必填配置时拒绝新增", False),
    "lanbridge_prepare_connector": ("POST", "connector/ensure", EMPTY, "检测或从官方下载 cloudflared 并保存路径", False),
    "lanbridge_create_tunnel": ("POST", "cloudflare/create-tunnel", EMPTY, "创建 Tunnel 或恢复连接令牌", False),
    "lanbridge_sync_turnstile": ("POST", "cloudflare/turnstile", EMPTY, "为已登记网站同步人类验证 Widget", False),
    "lanbridge_preview": ("POST", "cloudflare/preview", EMPTY, "读取并预览将发布的路由与 DNS", True),
    "lanbridge_apply": ("POST", "cloudflare/apply", {"type": "object", "properties": {"revision": {"type": "string", "minLength": 1}}, "required": ["revision"], "additionalProperties": False}, "发布指定预览 revision 并核验", False),
    "lanbridge_start_connector": ("POST", "connector/start", EMPTY, "启动本机连接器，使已发布网站可公网访问", False),
    "lanbridge_stop_connector": ("POST", "connector/stop", EMPTY, "停止本平台启动的连接器", False),
}


class Bridge:
    def __init__(self):
        base = os.environ.get("LANBRIDGE_ADMIN_URL", "http://127.0.0.1:8890").rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("MCP 只能连接本机 LanBridge 管理 API")
        self.client = httpx.Client(base_url=base, headers={"Origin": base}, timeout=180, trust_env=False)
        self.csrf = None

    def login(self):
        password = os.environ.get("LANBRIDGE_ADMIN_PASSWORD")
        if not password:
            raise ValueError("请通过 MCP 客户端的安全环境变量提供 LANBRIDGE_ADMIN_PASSWORD；不要放入工具参数")
        response = self.client.post("/api/login", json={"username": os.environ.get("LANBRIDGE_ADMIN_USERNAME", "admin"), "password": password})
        if not response.is_success:
            raise ValueError("MCP 管理员登录失败，请检查本机账号或稍后重试")
        self.csrf = response.json()["csrf"]

    def call(self, name, arguments):
        if name not in ROUTES:
            raise ValueError("未知工具")
        method, path, schema, _, _ = ROUTES[name]
        if not isinstance(arguments, dict) or set(arguments) - set(schema.get("properties", {})):
            raise ValueError("工具参数无效")
        if any(key not in arguments for key in schema.get("required", [])):
            raise ValueError("缺少必要参数")
        if name == "lanbridge_apply" and (not isinstance(arguments["revision"], str) or not arguments["revision"]):
            raise ValueError("请传入预览返回的 revision")
        if not self.csrf:
            self.login()
        response = self.client.request(method, "/api/" + path, **({"json": arguments, "headers": {"X-CSRF-Token": self.csrf}} if method == "POST" else {}))
        if response.status_code == 401:
            self.csrf = None
            raise ValueError("管理员会话已过期，请再次调用工具以重新登录")
        if not response.is_success:
            raise ValueError("LanBridge 拒绝操作，请在管理台查看配置、输入或发布状态")
        return response.json()


def handle(message, bridge, initialized):
    method, params = message.get("method"), message.get("params", {})
    if method == "initialize":
        requested = params.get("protocolVersion")
        supported = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
        return {"protocolVersion": requested if requested in supported else supported[-1], "capabilities": {"tools": {}}, "serverInfo": {"name": "LanBridge", "version": "1.0.0"}}
    if not initialized:
        raise ValueError("请先初始化 MCP 会话")
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": name, "description": route[3], "inputSchema": route[2], "annotations": {"readOnlyHint": route[4], "openWorldHint": True}} for name, route in ROUTES.items()]}
    if method == "tools/call":
        try:
            result = bridge.call(params.get("name"), params.get("arguments", {}))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}
        except ValueError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except (httpx.HTTPError, KeyError):
            return {"content": [{"type": "text", "text": "无法完成本机 API 请求，请检查管理台是否启动；写入结果未知时先核查状态"}], "isError": True}
    return None


def main():
    bridge, initialized = Bridge(), False
    try:
        for line in sys.stdin:
            message = None
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("无效请求")
                if "id" not in message:
                    continue
                result = handle(message, bridge, initialized)
                if message.get("method") == "initialize":
                    initialized = True
                response = {"jsonrpc": "2.0", "id": message["id"], **({"result": result} if result is not None else {"error": {"code": -32601, "message": "Method not found"}})}
            except (ValueError, TypeError, AttributeError):
                response = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None, "error": {"code": -32600, "message": "Invalid request"}}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        bridge.client.close()


if __name__ == "__main__":
    main()
