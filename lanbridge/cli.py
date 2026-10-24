"""Authenticated CLI access to every management API, without constructing Service."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from tempfile import NamedTemporaryFile
from urllib.parse import unquote, urlsplit
import httpx
from .cli_parser import SafeArgumentParser
from .cli_catalogue import endpoint_catalogue, field_value, secret_field

ROOT = Path(__file__).resolve().parent.parent
MAX_JSON = 8 * 1024 * 1024


class CliSessionChanged(ValueError):
    pass


def profile_digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).digest()
    except FileNotFoundError:
        return None


@contextmanager
def profile_lock(profile):
    """A short cross-process critical section; never held during HTTP calls."""
    with profile.with_suffix('.lock').open('a+b') as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + 2
        while True:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('CLI 会话正在被其他命令更新，请稍后重试') from None
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def forget_profile(profile):
    if profile.parent.exists():
        with profile_lock(profile):
            profile.unlink(missing_ok=True)


def base_url(value, data_dir):
    if not value:
        port = 8890
        runtime = data_dir / 'runtime.json'
        if runtime.exists():
            try:
                port = json.loads(runtime.read_text(encoding='utf-8'))['admin_port']
            except (KeyError, TypeError, ValueError, UnicodeError):
                raise ValueError('运行实例信息无效，请使用 --url 明确指定管理地址') from None
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError('运行实例端口无效')
        value = f'http://127.0.0.1:{port}'
    u = urlsplit(value)
    if (u.username or u.password or u.query or u.fragment or u.path not in ('', '/')
            or not u.hostname or u.scheme not in {'http', 'https'}
            or (u.scheme == 'http' and u.hostname not in {'127.0.0.1', 'localhost', '::1'})):
        raise ValueError('地址须为本机 HTTP 或 HTTPS 管理入口，不能含路径、凭据或查询参数')
    # Force port validation before any authentication.
    try:
        _ = u.port
        return str(httpx.URL(value).copy_with(path='')).rstrip('/')
    except (ValueError, httpx.InvalidURL):
        raise ValueError('管理地址格式无效，请核对域名和端口') from None


def api_path(value):
    u = urlsplit(value)
    decoded = unquote(u.path)
    if (u.scheme or u.netloc or u.fragment or not decoded.startswith('/api/')
            or '\\' in value or '\\' in decoded or any(part in {'.', '..'} for part in decoded.split('/'))
            or any(ord(c) < 32 for c in value) or '{' in value or '}' in value):
        raise ValueError('操作路径必须是 /api/ 开头的实际管理接口')
    return value


class OnlineClient:
    def __init__(self, url, profile_dir, *, token='', transport=None, timeout=60):
        self.url = url
        self.profile_dir = Path(profile_dir)
        self.profile = self.profile_dir / (hashlib.sha256(url.encode()).hexdigest() + '.bin')
        self.token = token
        self.csrf = ''
        self.profile_version = None
        self.profile_state = None
        self.client = httpx.Client(base_url=url, trust_env=False, follow_redirects=False,
                                   timeout=timeout, transport=transport)
        if token:
            self.client.headers['Authorization'] = 'Bearer ' + token
        else:
            try:
                self._load()
            except Exception:
                self.client.close()
                raise
        self.client.headers['Origin'] = url

    def _load(self):
        if not self.profile.exists():
            return
        from .store import dpapi
        if os.name != 'nt':
            raise ValueError('持久 CLI 登录目前仅支持 Windows DPAPI')
        if self.profile.stat().st_size > 262144:
            raise ValueError('CLI 会话文件无效，请重新登录')
        try:
            payload = self.profile.read_bytes()
            self.profile_version = hashlib.sha256(payload).digest()
            session = json.loads(dpapi(payload, True))
            if session['url'] != self.url:
                raise ValueError('会话地址不匹配')
            self.profile_state = session
            self.csrf = session.get('csrf', '')
            for cookie in session['cookies']:
                from http.cookiejar import Cookie
                self.client.cookies.jar.set_cookie(Cookie(version=0, name=cookie['name'], value=cookie['value'],
                    port=None, port_specified=False, domain=cookie['domain'], domain_specified=True,
                    domain_initial_dot=cookie['domain'].startswith('.'), path=cookie['path'], path_specified=True,
                    secure=cookie['secure'], expires=cookie['expires'], discard=cookie['expires'] is None,
                    comment=None, comment_url=None, rest={}))
        except (KeyError, TypeError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError('CLI 会话无法读取，请使用 logout --local 清除后重新登录') from exc

    def save(self):
        if self.token:
            return
        from .store import dpapi, protect_directory
        if os.name != 'nt':
            raise ValueError('持久 CLI 登录目前仅支持 Windows DPAPI')
        cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path,
                    'secure': c.secure, 'expires': c.expires} for c in self.client.cookies.jar]
        if not cookies:
            return
        state = {'url': self.url, 'csrf': self.csrf, 'cookies': cookies}
        payload = None
        if state != self.profile_state:
            protect_directory(self.profile_dir)
            payload = dpapi(json.dumps(state).encode())
        with profile_lock(self.profile):
            self._check_profile_version()
            if payload is None:
                return
            temporary = None
            try:
                with NamedTemporaryFile(dir=self.profile_dir, suffix='.tmp', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.profile)
                self.profile_version = hashlib.sha256(payload).digest()
                self.profile_state = state
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

    def _check_profile_version(self):
        if profile_digest(self.profile) != self.profile_version:
            raise CliSessionChanged('服务端请求已处理，但 CLI 会话已被另一命令更改；未覆盖或删除当前缓存，请核对状态，勿重复提交')

    def discard(self):
        if self.profile.parent.exists():
            with profile_lock(self.profile):
                self._check_profile_version()
                self.profile.unlink(missing_ok=True)
                self.profile_version = None
                self.profile_state = None


    def request(self, method, path, data=None, *, output=None):
        path = api_path(path)
        target = Path(output) if output else None
        stream = None
        download_complete = False
        if target:
            try:
                # Reserve the output before any remote operation, including parent/ACL checks.
                stream = target.open('xb')
            except OSError:
                raise ValueError('无法创建响应文件，未发送请求；文件可能已存在，或目录不存在/不可写') from None
        headers = {'X-CSRF-Token': self.csrf} if self.csrf and not self.token else {}
        try:
            with self.client.stream(method, path, json=data, headers=headers) as response:
                if response.is_redirect:
                    raise ValueError('管理入口返回重定向，请核对 --url；未向跳转地址发送凭据')
                if response.is_error:
                    raise ValueError(f'HTTP {response.status_code}：操作未完成；401 请重新登录，403 请核对权限，400 请核对输入和服务端操作记录')
                if stream:
                    # Keep only a bounded JSON copy to retain login/refresh CSRF metadata.
                    content = bytearray() if 'application/json' in response.headers.get('content-type', '').lower() else None
                    try:
                        for chunk in response.iter_bytes():
                            stream.write(chunk)
                            if content is not None:
                                if len(content) + len(chunk) <= MAX_JSON:
                                    content.extend(chunk)
                                else:
                                    content = None
                        stream.flush()
                        closing, stream = stream, None
                        closing.close()
                    except OSError:
                        raise ValueError('服务端已接受操作，但响应文件写入失败；请先查询状态，勿重复提交') from None
                    if content:
                        try:
                            metadata = json.loads(content)
                        except (ValueError, UnicodeError):
                            metadata = None
                        if isinstance(metadata, dict) and isinstance(metadata.get('csrf'), str):
                            self.csrf = metadata['csrf']
                    download_complete = True
                    result = {'saved_to': str(target.resolve())}
                else:
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_JSON:
                            raise ValueError('响应过大，请先查询操作状态；使用 --output 保存较大响应，勿盲目重复写入')
                    try:
                        result = json.loads(content) if content else {}
                    except (ValueError, UnicodeError):
                        raise ValueError('接口未返回 JSON，请先查询操作状态；可使用 --output 保存响应') from None
                    if isinstance(result, dict) and isinstance(result.get('csrf'), str):
                        self.csrf = result['csrf']
                if path.split('?', 1)[0] == '/api/logout':
                    self.discard()
                else:
                    try:
                        self.save()
                    except CliSessionChanged:
                        raise
                    except (OSError, RuntimeError, ValueError):
                        raise ValueError('操作已完成，但 CLI 会话保存失败；请重新登录，勿重复提交') from None
                return result
        finally:
            if target:
                try:
                    if stream:
                        stream.close()
                finally:
                    if not download_complete:
                        target.unlink(missing_ok=True)


    def close(self):
        self.client.close()


def redact(value):
    if isinstance(value, dict):
        return {key: ('[已隐藏，显式使用 --show-secrets 查看]' if isinstance(item, str) and item
                      and (key.lower() in {'token', 'csrf', 'password', 'passcode', 'secret', 'authority', 'access_token', 'refresh_token', 'authorization', 'code', 'access_key_id'}
                           or key.lower().endswith(('_token', '_secret', '_password'))) else redact(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def request_data(args):
    data = {}
    if args.file:
        if args.file == '-':
            raw = sys.stdin.read(20001)
        else:
            with Path(args.file).open(encoding='utf-8-sig') as stream:
                raw = stream.read(20001)
        if len(raw.encode('utf-8')) > 20000:
            raise ValueError('JSON 请求超过 20000 字节')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('JSON 请求必须是对象')
    for field in args.field:
        key, sep, raw = field.partition('=')
        if not sep or not key:
            raise ValueError('--field 使用 字段=值 格式')
        if secret_field(key.lower()):
            raise ValueError('秘密字段请使用 --secret 隐藏输入或 --file - 标准输入')
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        data[key] = value
    for key in args.secret:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError('秘密字段名无效')
        data[key] = getpass.getpass(key + '（隐藏输入）：')
    if len(json.dumps(data, ensure_ascii=False).encode()) > 20000:
        raise ValueError('JSON 请求超过 20000 字节')
    for key, value in vars(args).items():
        if key.startswith('value_'):
            data[key[6:]] = field_value(key[6:], value)
        elif key.startswith('prompt_') and value:
            data[key[7:]] = getpass.getpass(key[7:] + '（隐藏输入）：')
    if len(json.dumps(data, ensure_ascii=False).encode()) > 20000:
        raise ValueError('JSON 请求超过 20000 字节')
    return data


# Shortcuts are convenience only; api METHOD /api/path covers the entire catalogue.
SHORTCUTS = {
    'state': ('GET', '/api/state'), 'sites': ('GET', '/api/state'),
    'save': ('POST', '/api/sites'), 'pause': ('POST', '/api/sites/{id}/pause'),
    'resume': ('POST', '/api/sites/{id}/pause'), 'probe': ('POST', '/api/sites/{id}/probe'),
    'retry-publish': ('POST', '/api/sites'), 'protection': ('GET', '/api/state'),
    'settings': ('POST', '/api/settings'), 'credentials': ('POST', '/api/credentials'),
    'connector': ('POST', '/api/connector/{action}'), 'cloudflare': ('POST', '/api/cloudflare/{action}'),
    'domains': ('GET', '/api/domain-onboarding'), 'domain': ('POST', '/api/domain-onboarding/{action}'),
    'logs': ('GET', '/api/audit'), 'export-logs': ('GET', '/api/audit/export'),
    'tokens': ('GET', '/api/temporary-tokens'), 'issue-token': ('POST', '/api/temporary-tokens'),
    'revoke-token': ('POST', '/api/temporary-tokens/{id}/revoke'),
    'shutdown': ('POST', '/api/shutdown'), 'restart': ('POST', '/api/restart'),
}


def main(argv=None):
    parser = SafeArgumentParser(prog='lanbridge online', description='通过运行中的管理 API 操作全部功能；命令清单见 commands')
    parser.add_argument('--url', help='管理入口，默认读取所选数据目录的运行端口')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--profile-dir', type=Path, help='DPAPI 会话目录，默认 data/cli-sessions')
    parser.add_argument('--token', action='store_true', help='隐藏输入临时管理 Token；也可设置 LANBRIDGE_TOKEN')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--timeout', type=float, default=60)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('commands', help='列出每项功能的命令、参数和管理入口')
    sub.add_parser('login', help='登录管理员并加密保存会话，密码隐藏输入或 LANBRIDGE_PASSWORD')
    logout = sub.add_parser('logout', help='撤销服务端会话并清除本地会话')
    logout.add_argument('--local', action='store_true', help='仅清除失效/损坏的本地会话')
    sub.add_parser('setup', help='初始化管理员，密码隐藏输入')
    catalogue = endpoint_catalogue()
    operations = {item['command']: item for item in catalogue if item['path'] != '/api/launcher/control'}
    routes = SHORTCUTS | {name: (item['method'], item['path']) for name, item in operations.items() if name not in SHORTCUTS and name not in {'login', 'setup', 'logout'}}
    for name, route in [('api', None), *routes.items()]:
        operation = operations.get(name)
        command = sub.add_parser(name, help=operation['description'] if operation else None, description=(operation['description'] + '\n' + operation['method'] + ' ' + operation['path']) if operation else None)
        if route is None:
            command.add_argument('method', choices=['GET', 'POST', 'HEAD'])
            command.add_argument('path')
        elif '{id}' in route[1]:
            command.add_argument('id')
        elif '{action}' in route[1]:
            command.add_argument('action')
        elif operation:
            for parameter in operation['parameters']:
                command.add_argument('--' + parameter.replace('_', '-'), required=True, dest='path_' + parameter, metavar=parameter.upper())
        if operation:
            for key in operation['fields']:
                option = '--' + key.replace('_', '-')
                if secret_field(key):
                    command.add_argument(option, action='store_true', dest='prompt_' + key, help='隐藏输入 ' + key)
                else:
                    command.add_argument(option, dest='value_' + key, default=argparse.SUPPRESS, metavar=key.upper(), help='设置 ' + key)
            for key in operation['query']:
                command.add_argument('--' + key.replace('_', '-'), dest='query_' + key, default=argparse.SUPPRESS, metavar=key.upper(), help='查询参数 ' + key)
        command.add_argument('--file', help='JSON 文件；- 表示从标准输入读取')
        command.add_argument('--field', action='append', default=[], help='非秘密字段=值，可重复；值支持 JSON 标量/数组/对象')
        command.add_argument('--secret', action='append', default=[], help='通过隐藏输入设置秘密字段，可重复')
        command.add_argument('--output', type=Path, help='流式保存原始响应到新文件，不覆盖已有文件')
        command.add_argument('--show-secrets', action='store_true', help='显式显示返回的 Token 等秘密；自动化时避免记录输出')
    args = parser.parse_args(argv)
    client = None
    try:
        if args.command == 'commands':
            print(json.dumps({'schema': 'lanbridge-api-commands/v1', 'endpoints': catalogue}, ensure_ascii=False, indent=2))
            return 0
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('timeout 必须为有限的正数')
        url = base_url(args.url, args.data_dir)
        profile_dir = args.profile_dir or args.data_dir / 'cli-sessions'
        if args.command == 'logout' and args.local:
            forget_profile(profile_dir / (hashlib.sha256(url.encode()).hexdigest() + '.bin'))
            result = {'logged_out_locally': True}
        else:
            token = getpass.getpass('临时管理 Token（隐藏输入）：') if args.token else os.environ.get('LANBRIDGE_TOKEN', '')
            client = OnlineClient(url, profile_dir, token=token, timeout=args.timeout)
            if args.command in {'login', 'setup'}:
                if token:
                    raise ValueError('管理员登录/初始化请清除 LANBRIDGE_TOKEN，使用管理员身份')
                password = os.environ.get('LANBRIDGE_PASSWORD') or getpass.getpass('管理员密码（隐藏输入）：')
                if args.command == 'setup' and password != getpass.getpass('再次输入密码：'):
                    raise ValueError('两次密码不一致')
                client.request('POST', '/api/' + ('login' if args.command == 'login' else 'setup'), {'username': args.username, 'password': password})
                result = {'logged_in': True} if args.command == 'login' else {'initialized': True}
            elif args.command == 'logout':
                if token:
                    raise ValueError('logout 撤销 Cookie 会话；Token 请用 revoke-token')
                result = client.request('POST', '/api/logout', {})
            else:
                data = request_data(args)
                if args.command == 'api':
                    method, path = args.method, args.path
                else:
                    method, path = routes[args.command]
                    for key, value in vars(args).items():
                        if key.startswith('path_'):
                            if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
                                raise ValueError('ID 格式无效')
                            path = path.replace('{' + key[5:] + '}', value)
                    query = {key[6:]: value for key, value in vars(args).items() if key.startswith('query_')}
                    if 'refresh' in query:
                        if query['refresh'].lower() not in {'true', 'false', '1', '0'}:
                            raise ValueError('refresh 必须为 true、false、1 或 0')
                        query['refresh'] = '1' if query['refresh'].lower() in {'true', '1'} else '0'
                    if query:
                        from urllib.parse import urlencode
                        path += '?' + urlencode(query)
                    if hasattr(args, 'id'):
                        if not re.fullmatch(r'[A-Za-z0-9_-]+', args.id):
                            raise ValueError('ID 格式无效')
                        path = path.replace('{id}', args.id)
                    if hasattr(args, 'action'):
                        if not re.fullmatch(r'[a-z][a-z0-9-]*', args.action):
                            raise ValueError('action 格式无效')
                        path = path.replace('{action}', args.action)
                    if args.command in {'pause', 'resume'}:
                        data['paused'] = args.command == 'pause'
                    if args.command == 'retry-publish':
                        data.update(background=True, retry_publication=True)
                    if args.command in {'save', 'save-site'}:
                        data.setdefault('background', True)
                if method in {'GET', 'HEAD'} and data:
                    raise ValueError('GET/HEAD 不接受 JSON 请求体；查询参数写入路径')
                result = client.request(method, path, data if method == 'POST' else None, output=args.output)
                if args.command in {'sites', 'protection'} and not args.output:
                    if not isinstance(result, dict):
                        raise ValueError('状态接口返回格式无效，请核对管理地址')
                    result = result.get('sites' if args.command == 'sites' else 'visitor_protection')
                if not args.show_secrets:
                    result = redact(result)
        print(json.dumps({'schema': 'lanbridge-result/v1', 'ok': True, 'result': result}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as exc:
        # Transport exceptions can embed URLs or server-controlled text. Keep them generic.
        error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, (json.JSONDecodeError, UnicodeError)) else 'CLI 操作未完成，请检查输入、连接或会话；请求未自动重试'
        print(json.dumps({'schema': 'lanbridge-result/v1', 'ok': False, 'error': error}, ensure_ascii=False))
        return 1
    finally:
        if client:
            client.close()
