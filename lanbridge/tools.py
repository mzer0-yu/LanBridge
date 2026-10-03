"""Resolve and install the official Windows cloudflared executable."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from urllib.request import getproxies

import httpx

TOOL_DIR = Path(__file__).resolve().parents[1] / "bin"
RELEASE_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"


def version(path):
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode or "cloudflared version" not in result.stdout.lower():
        raise ValueError("该程序不是可用的 cloudflared，请检查路径")
    return result.stdout.strip()[:160]


def ensure_cloudflared(configured=""):
    if configured:
        path = Path(configured)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("指定路径不存在，请修正路径或清空后自动检测")
        return {"path": str(path), "source": "configured", "version": version(path)}
    found = shutil.which("cloudflared")
    local = TOOL_DIR / "cloudflared.exe"
    if found or local.is_file():
        path = Path(found) if found else local
        return {"path": str(path.resolve()), "source": "PATH" if found else "local", "version": version(path)}
    if os.name != "nt":
        raise ValueError("自动下载目前支持 Windows；请安装 cloudflared 或指定完整路径")
    machine = platform.machine().lower()
    arch = "amd64" if machine in ("amd64", "x86_64") else "386" if machine in ("x86", "i386", "i686") else None
    if not arch:
        raise ValueError("当前 CPU 架构暂不支持自动下载，请手动指定 cloudflared 路径")
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with httpx.Client(timeout=60, follow_redirects=True, proxy=getproxies().get("https"), headers={"User-Agent": "LanBridge"}) as client:
            response = client.get(RELEASE_API)
            response.raise_for_status()
            asset = next((a for a in response.json().get("assets", []) if a["name"] == f"cloudflared-windows-{arch}.exe"), None)
            if not asset or not asset.get("digest", "").startswith("sha256:"):
                raise ValueError("官方发布未提供可核验的安装包，请稍后重试或手动安装")
            url = asset["browser_download_url"]
            if not url.startswith("https://github.com/cloudflare/cloudflared/releases/download/"):
                raise ValueError("官方安装包地址无效")
            checksum, size = hashlib.sha256(), 0
            with tempfile.NamedTemporaryFile(dir=TOOL_DIR, suffix=".exe", delete=False) as handle:
                temp = Path(handle.name)
                with client.stream("GET", url) as download:
                    download.raise_for_status()
                    for chunk in download.iter_bytes():
                        size += len(chunk)
                        if size > 150 * 1024 * 1024:
                            raise ValueError("安装包超出允许大小")
                        checksum.update(chunk)
                        handle.write(chunk)
            if size != asset["size"] or checksum.hexdigest() != asset["digest"].split(":", 1)[1]:
                raise ValueError("安装包完整性校验失败，未安装，请重试")
            verified_version = version(temp)
            temp.replace(local)
            return {"path": str(local), "source": "download", "version": verified_version}
    except httpx.HTTPError:
        raise ValueError("无法连接 Cloudflare 官方下载源，请检查网络或代理后重试，也可手动填写路径") from None
    finally:
        if temp and temp.exists():
            temp.unlink()
