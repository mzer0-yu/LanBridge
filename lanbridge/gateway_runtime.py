from __future__ import annotations

import os
import socket
import threading
import time
from urllib.parse import urlsplit

from .models import Settings


class GatewayRuntime:
    """Own the optional gateway listener while the admin service stays available."""

    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.server = self.thread = self.socket = None
        self.error = ""
        self.port = service.settings()["gateway_port"]
        self.stopping = False

    def status(self):
        with self.lock:
            running = bool(self.server and self.server.started and self.thread.is_alive())
            return {"running": running, "port": self.port,
                    "error": self.error or ("转发网关已停止" if self.server and not running else "")}

    def start(self, port=None):
        import uvicorn
        from .gateway import create_gateway
        with self.lock, self.service.lock:
            if self.stopping:
                raise ValueError("平台正在退出")
            if self.status()["running"]:
                return self.status()
            if self.thread and self.thread.is_alive():
                raise ValueError("网关正在启动或停止，请稍后重试")
            cfg = self.service.settings()
            selected = port if port is not None else self.service.store.get("pending_gateway_port") or self.port
            candidate = Settings(**(cfg | {"gateway_port": selected})).model_dump()
            for site in self.service.sites():
                if site.get("target") != "lanbridge":
                    origin = urlsplit(site["origin"])
                    if (origin.port or (443 if origin.scheme == "https" else 80)) in (candidate["admin_port"], selected):
                        self.error = "网关端口与网站源站端口相同，请修改端口，避免转发循环。"
                        return self.status()
            self.port = selected
            listener = socket.socket()
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", selected))
                listener.listen(128)
            except OSError:
                listener.close()
                self.error = f"转发网关端口 {selected} 被占用或被系统保留。请修改端口后重试。"
                return self.status()
            try:
                values = {"settings": candidate, "pending_gateway_port": None}
                if selected != cfg["gateway_port"]:
                    if not self.service.store.get("previous_gateway_port"):
                        values["previous_gateway_port"] = cfg["gateway_port"]
                    if cfg["tunnel_id"]:
                        values["publication_error"] = "本机网关端口已修改，请在网站转发中点击重试发布，同步云端路由"
                self.service.store.set_many(values)
                self.server = uvicorn.Server(uvicorn.Config(create_gateway(self.service), host="127.0.0.1", port=selected, limit_concurrency=192, ws_max_size=8 * 1024 * 1024, ws_max_queue=2, h11_max_incomplete_event_size=16384, proxy_headers=False, access_log=False, log_config=None, timeout_graceful_shutdown=8, log_level="warning"))
                self.socket = listener
                self.error = ""
                def run():
                    try:
                        self.server.run(sockets=[listener])
                    finally:
                        listener.close()
                self.thread = threading.Thread(target=run, daemon=True)
                self.thread.start()
                deadline = time.monotonic() + 5
                while self.thread.is_alive() and not self.server.started and time.monotonic() < deadline:
                    time.sleep(0.02)
                if not self.server.started:
                    self.server.should_exit = True
                    self.error = "转发网关启动未完成，请稍后重试。"
                return self.status()
            except BaseException:
                listener.close()
                raise

    def stop(self):
        with self.lock:
            self.stopping = True
            if self.server:
                self.server.should_exit = True
            if self.thread:
                self.thread.join(timeout=10)
            if self.socket:
                self.socket.close()
