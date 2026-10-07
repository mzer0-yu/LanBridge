from __future__ import annotations
import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import sys
import threading

from lanbridge.service import Service
from lanbridge.models import Settings
from lanbridge.store import password_hash

ROOT = Path(__file__).resolve().parent


def acquire_runtime(root):
    handle = open(root / "runtime.lock", "a+b")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            raise ValueError("平台正在运行，请通过管理台/API 操作；CLI 写入需先停止平台") from None
    else:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ValueError("平台正在运行") from None
    return handle


def serve(service, open_browser=False, authorize_cloudflare=False, admin_port=None, gateway_port=None):
    import secrets
    import uvicorn
    from lanbridge.admin import create_admin
    from lanbridge.gateway_runtime import GatewayRuntime
    from lanbridge.gateway_log import configure_runtime_logging
    # Only the admin listener is required to enter the recovery interface.
    cfg = Settings(**(service.settings() | ({"admin_port": admin_port} if admin_port is not None else {}))).model_dump()
    runtime_log = configure_runtime_logging(service.store.root)
    listener = socket.socket()
    if os.name == "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    gateway = GatewayRuntime(service)
    service.gateway_runtime = gateway
    info_path = service.store.root / "runtime.json"
    try:
        try:
            listener.bind(("127.0.0.1", cfg["admin_port"]))
            listener.listen(128)
        except OSError:
            raise ValueError(f'管理台端口 {cfg["admin_port"]} 无法绑定，请在启动器中修改端口后重试。') from None
        previous = service.settings()
        if cfg != previous:
            values = {"settings": cfg, "sites": [dict(site, origin=f'http://127.0.0.1:{cfg["admin_port"]}') if site.get("target") == "lanbridge" else site for site in service.store.get("sites", [])]}
            service.store.set_many(values)
        result = gateway.start(gateway_port)
        if result["error"]:
            print(result["error"] + " 管理台仍可使用。", flush=True)
        service.runtime_id = secrets.token_hex(16)
        info = {"instance": service.runtime_id, "admin_port": cfg["admin_port"]}
        temporary = info_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(info), encoding="utf-8")
        temporary.replace(info_path)
        print(f'管理台：http://127.0.0.1:{cfg["admin_port"]}/admin', flush=True)
        admin = None
        def shutdown():
            admin.should_exit = True
        admin = uvicorn.Server(uvicorn.Config(create_admin(service, shutdown), host="127.0.0.1", port=cfg["admin_port"], proxy_headers=False, access_log=False, log_config=None, timeout_graceful_shutdown=8, log_level="warning"))
        if authorize_cloudflare:
            service.browser_auth.start()
        if open_browser:
            def show_browser():
                import time
                import webbrowser
                while not admin.started and not admin.should_exit:
                    time.sleep(0.1)
                if admin.started:
                    webbrowser.open(f'http://127.0.0.1:{cfg["admin_port"]}/admin')
            threading.Thread(target=show_browser, daemon=True).start()
        admin.run(sockets=[listener])
    finally:
        gateway.stop()
        if hasattr(service, "browser_auth"):
            service.browser_auth.stop()
        service.connector.stop()
        listener.close()
        info_path.unlink(missing_ok=True)
        runtime_log.close()


def open_existing(root, open_browser=True):
    """Confirm our runtime lock and instance marker, never just an occupied port."""
    import httpx
    import webbrowser
    if not root.exists():
        return False
    try:
        lock = acquire_runtime(root)
    except ValueError:
        try:
            info = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
            port = info["admin_port"]
            if not isinstance(info.get("instance"), str) or len(info["instance"]) != 32:
                return False
            if type(port) is not int or not 1024 <= port <= 65535:
                return False
            with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as client:
                response = client.get(f"http://127.0.0.1:{port}/api/bootstrap")
                payload = response.json()
                if response.status_code != 200 or not isinstance(payload, dict) or payload.get("instance") != info["instance"]:
                    return False
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{port}/admin")
            return True
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            return False
    else:
        lock.close()
        return False


def main():
    parser = argparse.ArgumentParser(description="LanBridge 管理平台 · CLI / API / MCP / SKILL")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    server = sub.add_parser("serve")
    server.add_argument("--open-browser", action="store_true")
    server.add_argument("--admin-port", type=int, help="启动前选择管理台端口（1024–65535），成功绑定后保存")
    server.add_argument("--gateway-port", type=int, help="启动前选择转发网关端口（1024–65535），成功绑定后保存")
    server.add_argument("--authorize-cloudflare", action="store_true", help="启动后打开官方浏览器授权，自动配置启用人类验证的网站")
    sub.add_parser("status")
    sub.add_parser("open-existing")
    sub.add_parser("setup-admin")
    credential = sub.add_parser("configure-secret")
    credential.add_argument("name", choices=["cf_read_token", "cf_write_token", "turnstile_secret"])
    config = sub.add_parser("configure")
    config.add_argument("file", type=Path)
    sub.add_parser("list-zones")
    zone = sub.add_parser("add-zone")
    zone.add_argument("file", type=Path)
    remove_zone = sub.add_parser("remove-zone")
    remove_zone.add_argument("zone_id")
    site = sub.add_parser("save-site")
    site.add_argument("file", type=Path)
    sub.add_parser("create-tunnel")
    provision = sub.add_parser("provision-token")
    provision.add_argument("--remember", action="store_true")
    provision.add_argument("--without-turnstile", action="store_true")
    provision.add_argument("--target", choices=["write", "read"], default="write")
    provision.add_argument("--repair-existing", action="store_true")
    provision.add_argument("--force-new", action="store_true", help="Create a new token even when one is managed")
    sub.add_parser("turnstile")
    sub.add_parser("preview")
    apply = sub.add_parser("apply")
    apply.add_argument("--revision", required=True)
    sub.add_parser("check")
    sub.add_parser("ensure-connector")
    args = parser.parse_args()
    if args.command == "capabilities":
        print(json.dumps({"schema": "lanbridge-capabilities/v1", "commands": list(sub.choices), "interfaces": ["cli", "api", "mcp", "skill"], "shared_business_core": True, "admin_loopback_only": True, "preview_required": True, "secrets_in_arguments": False}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "open-existing":
        return 0 if open_existing(args.data_dir.resolve()) else 1
    if args.command == "serve" and open_existing(args.data_dir.resolve(), args.open_browser):
        print("LanBridge 已运行，继续使用现有管理台。")
        return 0
    try:
        service = Service(args.data_dir.resolve())
        lock = acquire_runtime(service.store.root) if args.command != "status" else None
        try:
            if args.command == "serve":
                serve(service, args.open_browser, args.authorize_cloudflare, args.admin_port, args.gateway_port)
                return 0
            if args.command == "setup-admin":
                if service.store.get("admin"):
                    raise ValueError("管理员已存在，请在管理台修改密码")
                username = input("管理员用户名 [admin]：").strip() or "admin"
                password = getpass.getpass("管理员密码（至少 12 位）：")
                if password != getpass.getpass("再次输入："):
                    raise ValueError("两次密码不一致")
                service.store.set("admin", {"username": username, "password_hash": password_hash(password)})
                result = {"initialized": True}
            elif args.command == "configure-secret":
                value = getpass.getpass(f"输入 {args.name}（隐藏输入）：").strip()
                if len(value) < 10:
                    raise ValueError("令牌长度无效")
                with service.store.lock, service.store.db:
                    service.store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (args.name, service.store.cipher.encrypt(value.encode()).decode()))
                    if args.name == "cf_write_token":
                        service.store.db.execute("DELETE FROM secrets WHERE key=?", ("cf_oauth_profile",))
                        for key in ("managed_business_token", "pending_business_token", "pending_browser_token"):
                            service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
                result = {"saved": True}
            elif args.command == "configure":
                old = service.settings()
                data = json.loads(args.file.read_text(encoding="utf-8-sig"))
                if "zones" in data and data["zones"] != old["zones"]:
                    raise ValueError("请通过 add-zone / remove-zone 管理域名")
                data["zones"] = old["zones"]
                cfg = Settings(**data).model_dump()
                if old["account_id"] and old["zones"] and old["account_id"] != cfg["account_id"]:
                    raise ValueError("已接入域名属于当前账户，跨账户请使用独立数据目录")
                if old["tunnel_id"] and cfg["tunnel_id"] != old["tunnel_id"]:
                    raise ValueError("不能修改已登记 Tunnel ID")
                if cfg["tunnel_id"] and service.store.get("owned_tunnel") != cfg["tunnel_id"]:
                    raise ValueError("请通过 create-tunnel 创建独立 Tunnel")
                if old["tunnel_id"] and any(old[k] != cfg[k] for k in ("account_id", "zone_id", "zone_name")):
                    raise ValueError("已有 Tunnel，不能切换账户或 Zone")
                if old["zone_name"] != cfg["zone_name"] and service.sites():
                    raise ValueError("已有网站，不能切换 Zone")
                service.commit_settings(cfg)
                result = {"saved": True}
            elif args.command == "list-zones":
                result = {"attached": service.settings()["zones"], "available": service.cf.available_zones()}
            elif args.command == "add-zone":
                result = {"settings": service.add_zone(json.loads(args.file.read_text(encoding="utf-8-sig"))), "saved": True}
            elif args.command == "remove-zone":
                result = {"settings": service.remove_zone(args.zone_id), "saved": True}
            elif args.command == "save-site":
                data = json.loads(args.file.read_text(encoding="utf-8-sig"))
                if data.get("passcode_required"):
                    data["passcode"] = getpass.getpass("网站访问口令（新建至少 12 位，编辑留空保留）：")
                result = service.save_site(data, synchronize_verification=True)
            elif args.command == "status":
                result = {"settings": service.settings(), "pending_gateway_port": service.store.get("pending_gateway_port"), "sites": service.sites(), "cloudflare": service.store.get("cloudflare_status"), "note": "连接器实时进程状态请查管理台 API"}
            elif args.command == "create-tunnel":
                result = service.cf.create_tunnel()
            elif args.command == "provision-token":
                from lanbridge.token_manager import TokenManager
                authority = getpass.getpass("API Tokens Write 授权令牌（已有加密授权可留空）：")
                result = TokenManager(service).provision(authority, args.remember, not args.without_turnstile, args.target, args.repair_existing, args.force_new)
            elif args.command == "turnstile":
                result = service.cf.create_widget()
            elif args.command == "preview":
                result = service.cf.plan()
            elif args.command == "ensure-connector":
                result = service.connector.ensure()
            elif args.command == "apply":
                result = service.cf.apply(args.revision)
            else:
                result = service.cf.status()
                service.store.set("cloudflare_status", result)
            print(json.dumps({"schema": "lanbridge-result/v1", "ok": True, "result": result}, ensure_ascii=False, indent=2))
            return 0
        finally:
            if lock:
                lock.close()
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"schema": "lanbridge-result/v1", "ok": False, "error": str(exc) if len(str(exc)) < 400 else "配置无效"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
