"""Serve an explicitly selected local homepage and its static web directory."""
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import anyio
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

WEB_SUFFIXES = frozenset(".html .htm .css .js .mjs .json .map .svg .png .jpg .jpeg .gif .webp .avif .ico .bmp .woff .woff2 .ttf .otf .eot .txt .xml .pdf .webmanifest .wasm .mp4 .webm .mp3 .ogg .wav .vtt".split())


def is_static_origin(value):
    return isinstance(value, str) and value.strip().lower().startswith('file:')


def homepage_path(value):
    """Syntax only; never resolve a network share or probe user files here."""
    value = value.strip()
    parsed = urlsplit(value)
    if (len(value) > 4096 or parsed.scheme != 'file' or parsed.netloc
            or parsed.query or parsed.fragment or re.search(r'%(?![0-9a-fA-F]{2})', value)):
        raise ValueError('静态主页请填写 file:/// 开头的本机 HTML 文件 URL')
    decoded = unquote(parsed.path, errors='strict')
    if any(ord(c) < 32 for c in decoded) or '\\' in decoded or decoded.startswith('//'):
        raise ValueError('静态主页只支持本机文件，不支持网络共享或特殊路径')
    path = Path(url2pathname(parsed.path))
    if (not path.is_absolute() or path.suffix.lower() not in {'.html', '.htm'}
            or any(p.startswith('.') or p.endswith((' ', '.')) for p in path.parts[1:])
            or ':' in str(path)[len(path.anchor):]):
        raise ValueError('静态主页需要绝对路径的 HTML 文件 URL')
    return path


def _linked(path):
    attrs = path.lstat()
    return stat.S_ISLNK(attrs.st_mode) or bool(getattr(attrs, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def validate_homepage(origin, private_roots=()):
    path = homepage_path(origin)
    root = path.parent
    protected = [Path(__file__).resolve().parent.parent / name for name in ('data', 'bin', '.venv', '.git', '.test-artifacts')]
    protected.extend(Path(p) for p in private_roots)
    # A broad parent directory could otherwise expose the application's private data.
    resolved_root = root.resolve()
    if root == Path(root.anchor) or any(resolved_root.is_relative_to(p.resolve()) or p.resolve().is_relative_to(resolved_root) for p in protected):
        raise ValueError('请使用独立网页目录，不能托管磁盘根目录或平台私有数据目录')
    try:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if _linked(current):
                raise ValueError('静态网页目录和主页不能使用符号链接或目录联接')
        if not path.is_file():
            raise OSError('not a file')
        with path.open('rb') as source:
            source.read(1)
    except OSError:
        raise ValueError('静态主页不存在或无法读取，请检查本机文件 URL') from None
    return path


class WebDirectory(StaticFiles):
    def lookup_path(self, path):
        parts = path.replace(os.sep, '/').split('/')
        if (any(p in {'.', '..'} or p.startswith('.') or p.endswith((' ', '.')) for p in parts if p)
                or '\\' in '/'.join(parts) or ':' in path or '\x00' in path):
            return '', None
        current = Path(self.directory)
        try:
            for part in parts:
                current /= part
                if _linked(current):
                    return '', None
            if current.is_file() and current.suffix.lower() not in WEB_SUFFIXES:
                return '', None
            return super().lookup_path(path)
        except (OSError, ValueError):
            return '', None


async def static_response(request, origin, private_roots=()):
    if request.method not in {'GET', 'HEAD'}:
        return PlainTextResponse('静态网页只支持 GET / HEAD', 405, headers={'Allow': 'GET, HEAD'})
    try:
        home = await anyio.to_thread.run_sync(validate_homepage, origin, private_roots)
    except (ValueError, OSError):
        return PlainTextResponse('静态网页暂时不可用，请联系管理员', 503, headers={'Cache-Control': 'no-store'})
    files = WebDirectory(directory=home.parent, html=True, check_dir=False, follow_symlink=False)
    if '\\' in request.url.path:
        return PlainTextResponse('静态文件不存在', 404)
    path = home.name if request.url.path == '/' else request.url.path.lstrip('/')
    try:
        response = await files.get_response(path, request.scope)
    except HTTPException as exc:
        response = PlainTextResponse('静态文件不存在' if exc.status_code == 404 else '静态文件无法访问', exc.status_code)
    response.headers['Cache-Control'] = 'private, no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
