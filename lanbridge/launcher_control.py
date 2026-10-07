"""Authenticated loopback lifecycle control for the same user's native launcher."""
import json
from pathlib import Path
import re
import time
import httpx


def control_instance(root: Path, instance: str, operation: str, *, timeout=45):
    from run import acquire_runtime, open_existing
    if operation not in ('stop', 'restart') or not re.fullmatch(r'[a-f0-9]{32}', instance):
        raise ValueError('实例或操作无效')
    info = json.loads((root / 'runtime.json').read_text(encoding='utf-8'))
    if info.get('instance') != instance or not info.get('launcher_control') or not open_existing(root, open_browser=False):
        raise ValueError('实例已变化或不支持启动器控制')
    port = info['admin_port']
    token = info['launcher_control']
    if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        raise ValueError('控制身份格式无效')
    try:
        with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as client:
            response = client.post(f'http://127.0.0.1:{port}/api/launcher/control',
                headers={'X-LanBridge-Control':token, 'X-LanBridge-Instance':instance},
                json={'operation':operation})
            response.raise_for_status()
    except httpx.HTTPError:
        raise ValueError('控制请求未确认，请重新检测') from None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if operation == 'stop':
            try:
                handle = acquire_runtime(root)
            except ValueError:
                pass
            else:
                handle.close()
                return {'operation':operation, 'completed':True}
        else:
            try:
                current = json.loads((root / 'runtime.json').read_text(encoding='utf-8'))
                if current.get('instance') != instance and open_existing(root, open_browser=False):
                    return {'operation':operation, 'completed':True, 'port':current['admin_port']}
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(.2)
    raise ValueError('操作已提交，仍未确认完成，请重新检测')
