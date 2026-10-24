"""Discover CLI operation coverage and fields from management routes and models."""
import ast
import math
from pathlib import Path
from .models import Settings, Site, Zone

ROOT = Path(__file__).resolve().parent.parent
NAMES = {
    ('GET', '/api/audit'): 'logs', ('GET', '/api/audit/export'): 'export-logs',
    ('POST', '/api/audit/settings'): 'log-settings',
    ('POST', '/api/sites'): 'save-site',
    ('POST', '/api/sites/{site_id}/pause'): 'site-pause-mode',
    ('POST', '/api/sites/{site_id}/probe'): 'site-probe',
    ('GET', '/api/temporary-tokens'): 'tokens', ('POST', '/api/temporary-tokens'): 'issue-token',
    ('POST', '/api/temporary-tokens/{token_id}/revoke'): 'token-revoke',
    ('POST', '/api/temporary-tokens/{token_id}/reveal'): 'token-reveal',
    ('POST', '/api/zones'): 'add-zone',
    ('GET', '/api/domain-onboarding'): 'domains',
    ('POST', '/api/password'): 'change-password',
    ('POST', '/api/launcher/control'): 'control',
}
LABELS = {
    'admin-port': '设置管理端口', 'gateway-port': '设置转发网关端口',
    'gateway-retry': '恢复本机网关', 'public-client': '启用或关闭公网转发列表',
    'connector-auto-start': '设置启动时自动连接', 'change-password': '修改管理员密码',
    'save-site': '保存网站、静态托管与访问策略', 'site-pause-mode': '设置本机暂停与 Cloudflare 阻断方式',
    'site-probe': '探测源站', 'log-settings': '设置操作日志容量',
    'add-zone': '接入域名', 'zones-default': '设置默认域名', 'zones-remove': '移除域名',
    'token-reveal': '查看已签发的临时 Token', 'token-revoke': '撤销临时 Token',
    'client-routes': '查看公网网站列表', 'bootstrap': '查看登录和初始化状态',
}
LABELS.update({
    'logs': '查看操作日志', 'export-logs': '导出操作日志',
    'cloudflare-apply': '应用预览的云端变更并核验', 'cloudflare-preview': '预览 DNS 和路由变更',
    'cloudflare-browser-authorize': '发起 Cloudflare 官方浏览器授权',
    'cloudflare-browser-authorize-cancel': '取消当前 Cloudflare 授权',
    'cloudflare-browser-authorize-refresh': '续期 Cloudflare 授权',
    'cloudflare-browser-authorize-restart': '重新发起 Cloudflare 授权',
    'cloudflare-browser-authorize-select-zone': '选择授权域名',
    'cloudflare-browser-authorize-setup': '继续授权后的隧道配置',
    'cloudflare-check': '检查云端连接', 'cloudflare-create-tunnel': '创建或恢复专用隧道令牌',
    'cloudflare-forget-token-authority': '移除本机保存的令牌管理授权',
    'cloudflare-provision-token': '创建或修复业务 API Token',
    'cloudflare-token-connect': '确认并接入令牌对应的账户与域名',
    'cloudflare-token-discover': '发现令牌可访问的账户与域名',
    'cloudflare-turnstile': '同步人类验证域名', 'cloudflare-turnstile-auto': '自动准备人类验证',
    'cloudflare-zones': '查询账户可接入的域名',
    'connector-check-update': '检查连接器更新', 'connector-ensure': '检测或准备连接器',
    'connector-start': '启动连接器', 'connector-stop': '停止连接器', 'connector-update': '更新连接器',
    'credentials': '保存或移除访问凭据', 'domains': '查看域名接入进度',
    'domain-onboarding-backup': '下载域名 DNS 备份', 'domain-onboarding-cancel': '取消域名接入',
    'domain-onboarding-check': '检查域名接入结果', 'domain-onboarding-confirm': '确认域名 DNS 迁移',
    'domain-onboarding-connected-domains': '查看已接入的阿里云域名',
    'domain-onboarding-credentials': '保存阿里云授权凭据', 'domain-onboarding-dismiss-error': '收起历史接入错误',
    'domain-onboarding-finish': '结束域名接入跟踪', 'domain-onboarding-help': '下载域名接入说明',
    'domain-onboarding-oauth': '发起阿里云官方授权', 'domain-onboarding-prepare': '预览域名接入变更',
    'control': '控制已核验的本机实例', 'local-login-approve': '确认或拒绝本机登录请求',
    'local-login-browsers': '列出本机授权浏览器', 'local-login-cancel': '取消本机登录请求',
    'local-login-open': '打开本机登录确认页', 'local-login-poll': '查询并领取本机登录会话',
    'local-login-request': '查看本机登录请求', 'local-login-start': '发起本机登录请求',
    'login': '登录管理员', 'logout': '注销管理员会话', 'restart': '重启 LanBridge',
    'settings': '修改平台配置与人类验证设置', 'setup': '初始化管理员', 'shutdown': '关闭 LanBridge',
    'state': '查看全部状态和访问防护统计', 'tokens': '列出临时管理令牌',
    'issue-token': '签发临时管理令牌', 'token-login': '使用临时 Token 登录',
})
SECRETS = {'current', 'password', 'passcode', 'token', 'secret', 'authority',
           'code', 'access_key_id', 'access_key_secret', 'security_token', 'cf_read_token',
           'cf_write_token', 'turnstile_secret'}
ARRAYS = {'protocols', 'allowed_countries', 'allowed_ips', 'permissions'}
BOOLEANS = {'enabled', 'paused', 'cloudflare', 'background', 'retry_publication', 'human_check',
            'passcode_required', 'remember', 'repair_existing', 'force_new', 'require_waf', 'allow',
            'remove_cf_read_token', 'save_token'}
NUMBERS = {'port', 'hours', 'limit_mb', 'checked_at', 'requests_per_minute', 'session_minutes', 'human_remember_days'}


def secret_field(key):
    return key not in BOOLEANS and (key in SECRETS or key in {'access_token', 'refresh_token'} or key.endswith(('_token', '_secret', '_password')))


def _request_fields(node):
    return {call.args[0].value for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id in {'data', 'values'}
            and call.func.attr in {'get', 'pop'} and call.args
            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)}


def _actions(node):
    actions = set()
    for compare in ast.walk(node):
        if (isinstance(compare, ast.Compare) and isinstance(compare.left, ast.Name)
                and compare.left.id == 'action' and len(compare.ops) == 1):
            other = compare.comparators[0]
            if isinstance(compare.ops[0], ast.Eq) and isinstance(other, ast.Constant) and isinstance(other.value, str):
                actions.add(other.value)
            if isinstance(compare.ops[0], ast.In) and isinstance(other, (ast.Set, ast.Tuple, ast.List)):
                actions.update(item.value for item in other.elts if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return sorted(actions)


def _action_fields(node, action):
    # Only collect fields from the selected branch, excluding unrelated actions.
    for branch in ast.walk(node):
        if isinstance(branch, ast.If) and isinstance(branch.test, ast.Compare):
            compare = branch.test
            if not (isinstance(compare.left, ast.Name) and compare.left.id == 'action'):
                continue
            try:
                values = ast.literal_eval(compare.comparators[0])
            except (ValueError, TypeError):
                continue
            if (isinstance(compare.ops[0], ast.Eq) and values == action
                    or isinstance(compare.ops[0], ast.In) and action in values):
                return set().union(*(_request_fields(child) for child in branch.body))
    return set()


def endpoint_catalogue():
    result = []
    tree = ast.parse((ROOT / 'lanbridge' / 'admin.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == 'app'
                    and decorator.func.attr in {'get', 'post'} and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)):
                continue
            template = decorator.args[0].value
            if not template.startswith('/api/'):
                continue
            method = decorator.func.attr.upper()
            for action in _actions(node) if '{action}' in template else [None]:
                path = template.replace('{action}', action) if action else template
                fields = _action_fields(node, action) if action else _request_fields(node)
                if path == '/api/sites': fields |= set(Site.model_fields) - {'policy_version'} | {'passcode'}
                if path == '/api/settings': fields |= set(Settings.model_fields) - {'admin_port', 'gateway_port', 'tunnel_id', 'zones'}
                if path == '/api/credentials': fields |= {'cf_write_token', 'turnstile_secret'}
                if path == '/api/zones': fields |= set(Zone.model_fields)
                if path == '/api/cloudflare/token-connect': fields |= {'token', 'account_id', 'zone_id', 'zone_name', 'revision', 'save_token', 'cf_read_token'}
                if path == '/api/domain-onboarding/credentials': fields |= {'access_key_id', 'access_key_secret', 'security_token'}
                params = [part[1:-1] for part in path.split('/') if part.startswith('{')]
                name = NAMES.get((method, path), '-'.join(part for part in path.split('/')[2:] if not part.startswith('{')))
                query = ['before'] if path == '/api/audit' else ['refresh'] if path == '/api/domain-onboarding/connected-domains' else []
                if method == 'GET': fields = set()
                result.append({'method': method, 'path': path, 'command': name,
                               'entry': 'lanbridge control' if path == '/api/launcher/control' else 'lanbridge online ' + name,
                               'description': LABELS.get(name, name.replace('-', ' ')),
                               'parameters': params, 'fields': sorted(fields), 'query': query})
    return sorted(result, key=lambda item: (item['path'], item['method']))


def field_value(key, raw):
    import json
    if key in ARRAYS:
        return json.loads(raw) if raw.lstrip().startswith('[') else [item.strip() for item in raw.split(',') if item.strip()]
    if key in BOOLEANS:
        if raw.lower() not in {'true', 'false'}:
            raise ValueError(key + ' 必须为 true 或 false')
        return raw.lower() == 'true'
    if key in NUMBERS:
        try:
            number = float(raw) if key in {'checked_at', 'hours', 'limit_mb'} else int(raw)
            if isinstance(number, float) and not math.isfinite(number):
                raise ValueError
            return number
        except ValueError:
            raise ValueError(key + ' 必须为数字') from None
    return raw
