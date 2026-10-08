"""Local-only, persisted Aliyun -> Cloudflare onboarding with explicit NS confirmation."""
from __future__ import annotations
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import shlex
import threading
import time
from urllib.parse import quote, urlencode

import httpx
from .models import Zone


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def domain_name(value):
    if not isinstance(value, str):
        raise ValueError('请输入独立域名')
    name = value.strip().lower().rstrip('.')
    if len(name) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', name):
        raise ValueError('请输入完整独立域名，例如 example.com')
    return name


def nameservers(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 13:
        raise ValueError('DNS 服务器列表不完整，停止迁移')
    if not all(isinstance(v, str) for v in values):
        raise ValueError('DNS 服务器列表格式不正确')
    result = sorted(set(domain_name(n) for v in values for n in v.split(',')))
    if len(result) > 13:
        raise ValueError('DNS 服务器过多')
    return result


def dns_record(row, domain):
    if row.get('Status') != 'ENABLE' or row.get('Line') != 'default' or row.get('LbaStatus'):
        raise ValueError('存在停用、特殊线路或负载均衡解析，需先人工处理')
    kind = row.get('Type')
    if kind not in {'A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'CAA', 'SRV'}:
        raise ValueError('存在不支持自动迁移的解析类型，需先人工处理')
    rr = row.get('RR')
    if not isinstance(rr, str) or not rr or len(rr) > 253 or any(c.isspace() for c in rr):
        raise ValueError('解析主机记录格式不正确')
    name = domain if rr == '@' else rr.lower().rstrip('.') + '.' + domain
    if len(name) > 253 or not re.fullmatch(r'[a-z0-9_*.-]+', name):
        raise ValueError('解析主机记录格式不正确')
    if kind == 'NS' and rr == '@':
        raise ValueError('存在根域 NS 记录，需先人工处理')
    value = row.get('Value')
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('解析值格式不正确')
    ttl = row.get('TTL')
    if type(ttl) is not int or not 60 <= ttl <= 86400:
        raise ValueError('解析 TTL 无法原样迁移，需先人工处理')
    result = {'type': kind, 'name': name, 'ttl': ttl, 'content': value, 'proxied': False}
    if kind in {'A', 'AAAA'}:
        try:
            if ipaddress.ip_address(value).version != (4 if kind == 'A' else 6):
                raise ValueError()
        except ValueError:
            raise ValueError('解析 IP 地址格式不正确') from None
    if kind in {'CNAME', 'MX', 'NS'}:
        result['content'] = domain_name(value)
    if kind == 'MX':
        priority = row.get('Priority')
        if type(priority) is not int or not 0 <= priority <= 65535:
            raise ValueError('MX 优先级无效')
        result['priority'] = priority
    if kind == 'CAA':
        parts = shlex.split(value)
        if len(parts) != 3 or not parts[0].isdigit() or not 0 <= int(parts[0]) <= 255 or not re.fullmatch(r'[a-zA-Z0-9]+', parts[1]):
            raise ValueError('CAA 解析值无法安全转换')
        result['data'] = {'flags': int(parts[0]), 'tag': parts[1], 'value': parts[2]}
        result.pop('content')
    if kind == 'SRV':
        parts = value.split()
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 65535 for p in parts[:3]):
            raise ValueError('SRV 解析值无法安全转换')
        result['data'] = dict(zip(('priority', 'weight', 'port'), map(int, parts[:3])))
        result['data']['target'] = domain_name(parts[3])
        result.pop('content')
    return result


def record_identity(row):
    kind = row.get('type')
    data = row.get('data') if kind in {'CAA', 'SRV'} else None
    if data:
        keys = ('flags', 'tag', 'value') if kind == 'CAA' else ('priority', 'weight', 'port', 'target')
        content = {key: data.get(key) for key in keys}
        if kind == 'SRV' and isinstance(content['target'], str):
            content['target'] = content['target'].lower().rstrip('.')
    else:
        content = row.get('content')
        if kind in {'CNAME', 'MX', 'NS'} and isinstance(content, str):
            content = content.lower().rstrip('.')
    return [kind, str(row.get('name', '')).lower().rstrip('.'), content, row.get('priority') if kind == 'MX' else None]


class Aliyun:
    def __init__(self, store):
        self.store = store
        from .aliyun_oauth import AliyunOAuth
        self.oauth = AliyunOAuth(store)

    def call(self, product, action, **parameters):
        if product not in {'domain', 'alidns'}:
            raise ValueError('无效阿里云服务')
        oauth = self.store.get('aliyun_auth_mode') == 'oauth'
        key, secret, security_token = self.oauth.credentials() if oauth else tuple(self.store.secret(k) for k in ('aliyun_access_key_id','aliyun_access_key_secret','aliyun_security_token'))
        if not key or not secret:
            raise ValueError('请先保存阿里云 RAM 凭据')
        values = {'Action': action, 'Version': '2018-01-29' if product == 'domain' else '2015-01-09',
                  'Format': 'JSON', 'AccessKeyId': key, 'SignatureMethod': 'HMAC-SHA1',
                  'SignatureVersion': '1.0', 'SignatureNonce': secrets.token_hex(16),
                  'Timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), **parameters}
        if security_token:
            values['SecurityToken'] = security_token
        encode = lambda value: quote(str(value), safe='~')
        canonical = '&'.join(encode(k) + '=' + encode(values[k]) for k in sorted(values))
        signing = 'POST&%2F&' + encode(canonical)
        values['Signature'] = base64.b64encode(hmac.new((secret + '&').encode(), signing.encode(), hashlib.sha1).digest()).decode()
        try:
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
                response = client.post('https://' + product + '.aliyuncs.com/',
                                       content=urlencode(values), headers={'Content-Type': 'application/x-www-form-urlencoded'})
            payload = response.json()
            if not isinstance(payload, dict) or response.status_code != 200 or payload.get('Code'):
                raise RuntimeError('阿里云请求被拒绝，请检查 RAM 权限、域名状态及凭据有效期')
            return payload
        except (httpx.HTTPError, ValueError):
            raise RuntimeError('阿里云网络或响应异常；写入结果可能尚未确认，请检查任务状态') from None


class DomainOnboarding:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.aliyun = Aliyun(self.store)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.inventory_lock = threading.Lock()
        self.inventory_cache = None

    def connected_domains(self, *, refresh=False):
        with self.inventory_lock:
            with self.service.lock:
                cfg = self.service.settings()
                context = fingerprint([self._context(), cfg['zones'], cfg['zone_id']])
                configured = bool(self.store.secret('aliyun_oauth_profile') if self.store.get('aliyun_auth_mode') == 'oauth' else self.store.secret('aliyun_access_key_id') and self.store.secret('aliyun_access_key_secret'))
            if not configured:
                return {'configured': False, 'domains': [], 'checked_at': None}
            cached = self.inventory_cache
            if not refresh and cached and cached['context'] == context and time.time() - cached['checked_at'] < 300:
                names, checked_at = cached['names'], cached['checked_at']
            else:
                wanted = {z['zone_name'] for z in cfg['zones']}
                names, checked_at = set(), time.time()
                deadline = time.monotonic() + 25
                if wanted:
                    try:
                        for page in range(1, 101):
                            if time.monotonic() > deadline:
                                raise ValueError('域名查询超时')
                            payload = self._ali('domain', 'QueryDomainList', PageNum=page, PageSize=100)
                            rows = payload.get('Data', {}).get('Domain')
                            total = payload.get('TotalPageNum')
                            if not isinstance(rows, list) or not isinstance(total, int) or isinstance(total, bool) or total < 0:
                                raise ValueError('域名列表响应无效')
                            for row in rows:
                                if not isinstance(row, dict):
                                    raise ValueError('域名列表响应无效')
                                names.add(domain_name(row.get('DomainName')))
                            if wanted.issubset(names) or page >= total:
                                break
                        else:
                            raise ValueError('域名列表超过查询范围')
                    except (RuntimeError, ValueError, AttributeError, TypeError):
                        raise ValueError('读取阿里云域名列表失败，请检查网络、授权及域名查询权限后重试。已有接入配置未改动。') from None
                with self.service.lock:
                    current = self.service.settings()
                    if fingerprint([self._context(), current['zones'], current['zone_id']]) != context:
                        raise ValueError('账户或已接入域名已变化，请刷新列表')
                    self.inventory_cache = {'context': context, 'names': names, 'checked_at': checked_at}
            with self.service.lock:
                current = self.service.settings()
                if fingerprint([self._context(), current['zones'], current['zone_id']]) != context:
                    raise ValueError('账户或已接入域名已变化，请刷新列表')
                return {'configured': True, 'checked_at': checked_at, 'domains': [
                    z | {'default': z['zone_id'] == current['zone_id']}
                    for z in current['zones'] if z['zone_name'] in names]}

    def _job(self):
        raw = self.store.secret('domain_onboarding_job')
        return json.loads(raw) if raw else None

    def _save(self, job):
        self.store.set_secret('domain_onboarding_job', json.dumps(job, ensure_ascii=False))

    def _context(self):
        owned = self.store.get('managed_business_token') or {}
        credential = (['oauth', owned.get('account_id'), owned.get('zone_id'), owned.get('scopes')]
                      if owned.get('kind') == 'oauth' else self.store.secret('cf_write_token'))
        return fingerprint([self.service.settings()['account_id'], self.store.get('admin', {}).get('password_hash'),
                            credential, (['oauth',self.store.get('aliyun_oauth_identity')] if self.store.get('aliyun_auth_mode')=='oauth' else [self.store.secret(k) for k in ('aliyun_access_key_id', 'aliyun_access_key_secret', 'aliyun_security_token')])])

    def _ensure_running(self):
        if self.stop_event.is_set():
            raise RuntimeError('平台正在退出，已停止后续迁移操作')

    def _ali(self, product, action, **params):
        self._ensure_running()
        return self.aliyun.call(product, action, **params)

    def _check_context(self, job):
        if job['account_id'] != self.service.settings()['account_id'] or job['context'] != self._context():
            raise ValueError('账户或凭据已变化，请重新准备迁移')

    def save_credentials(self, values):
        with self.lock:
            job = self._job()
            if job and job['phase'] in {'switching', 'waiting', 'uncertain'}:
                raise ValueError('迁移尚未结束，暂不可替换凭据')
            key, secret, token = (values.get(k, '') for k in ('access_key_id', 'access_key_secret', 'security_token'))
            if not all(isinstance(v, str) and len(v) <= 4096 and not re.search(r'\s', v) for v in (key, secret, token)):
                raise ValueError('阿里云凭据格式不正确')
            if not key or not secret:
                raise ValueError('需要同时填写 AccessKey ID 和 AccessKey Secret')
            self.store.set_many({'aliyun_auth_mode':'manual','aliyun_oauth_identity':None}, secret_values={'aliyun_oauth_profile':'','aliyun_access_key_id': key, 'aliyun_access_key_secret': secret, 'aliyun_security_token': token})
            self.store.audit('aliyun_credentials_saved', {})
            return self.status()

    def authorize_aliyun(self):
        with self.lock:
            job = self._job()
            if job and job['phase'] in {'switching', 'waiting', 'uncertain'}:
                raise ValueError('迁移尚未结束，暂不可替换授权')
            context = [self.service.settings()['account_id'], self.store.get('admin', {}).get('password_hash')]
            profile, identity = self.aliyun.oauth.authorize()
            if context != [self.service.settings()['account_id'], self.store.get('admin', {}).get('password_hash')]:
                raise ValueError('管理员或账户已变化，请在当前会话重新授权')
            self.store.set_many({'aliyun_auth_mode':'oauth','aliyun_oauth_identity':identity}, secret_values={'aliyun_oauth_profile':json.dumps(profile),'aliyun_access_key_id':'','aliyun_access_key_secret':'','aliyun_security_token':''})
            self.store.audit('aliyun_oauth_connected', {})
            return self.status()

    def _cf(self, method, path, body=None):
        self._ensure_running()
        # New zones may not be included in the optional read token's resource scope.
        return self.service.cf.request(method, path, body, force_write=True)

    @staticmethod
    def _compatible(source, target):
        identities = {fingerprint(record_identity(row)): row for row in source}
        for existing in target:
            row = identities.get(fingerprint(record_identity(existing)))
            if row is None:
                raise ValueError('Cloudflare 已有额外或冲突解析；不覆盖，请先人工核对')
            if existing.get('proxied', False) or existing.get('ttl') != row['ttl']:
                raise ValueError('已有解析的 TTL 或代理状态不同，请人工核对')

    def backup(self):
        with self.lock:
            job = self._job()
            if not job:
                raise ValueError('没有可导出的任务备份')
            key = 'domain_backup_' + hashlib.sha256(job['domain'].encode()).hexdigest()
            raw = self.store.secret(key)
            return json.loads(raw) if raw else {'domain': job['domain'], 'at': job['created_at'],
                'nameservers': job['old_nameservers'], 'source': job['source'], 'cloudflare': job['target']}

    def dismiss_prepare_failure(self, checked_at):
        with self.lock, self.service.lock, self.store.lock:
            job = self._job()
            if job and job['phase'] not in {'done', 'closed', 'cancelled'}:
                raise ValueError('已有域名接入任务，请使用任务的取消或结束跟踪入口')
            key = fingerprint(['POST', '/zones'])
            issues = self.store.get('cloudflare_permission_issues', {})
            issue = issues.get(key)
            if not issue or issue.get('checked_at') != checked_at:
                raise ValueError('接入失败记录已变化，请刷新后核对')
            self.store.audit('domain_onboarding_failure_dismissed', {'operation':'创建 Cloudflare 区域',
                'http_status':issue.get('http_status'), 'failed_at':issue['checked_at']})
            issues.pop(key)
            self.store.set('cloudflare_permission_issues', issues)
            return {'saved':True, 'cloudflare_permission_issues':self.service.permission_issues()}

    def finish_tracking(self, identifier, confirmed_domain):
        with self.lock:
            job = self._job()
            if not job or job['id'] != identifier or domain_name(confirmed_domain) != job['domain']:
                raise ValueError('任务确认不匹配')
            if job['phase'] not in {'uncertain', 'switching', 'waiting'}:
                raise ValueError('此任务无需结束跟踪')
            job.update(phase='closed', message='已结束跟踪；云端变更不会撤销，请到阿里云和 Cloudflare 核对')
            self._save(job)
            self.store.audit('domain_onboarding_tracking_closed', {'domain': job['domain']})
            return self.status()

    def _source(self, domain):
        detail = self._ali('domain', 'QueryDomainByDomainName', DomainName=domain)
        if detail.get('DomainName') != domain:
            raise ValueError('阿里云返回域名不匹配')
        if detail.get('UpdateProhibitionLock') not in {'CLOSE', 'NONE_SETTING'}:
            raise ValueError('域名更新锁未关闭或状态未知，请先在阿里云核对')
        if detail.get('ExpirationDateStatus') != '1' or detail.get('DomainStatus') != '3':
            raise ValueError('域名非正常有效状态，请先在阿里云处理')
        if detail.get('TransferOutStatus') != 'NORMAL' or detail.get('RegistrantUpdatingStatus') != 'NORMAL' or detail.get('EmailVerificationClientHold') is not False:
            raise ValueError('域名转出、持有者变更或验证状态不明确，请先在阿里云处理')
        ns = nameservers(detail.get('DnsList', {}).get('Dns'))
        ds = self._ali('domain', 'QueryDSRecord', DomainName=domain)
        if 'DSRecordList' not in ds or not isinstance(ds['DSRecordList'], list):
            raise ValueError('无法确认 DNSSEC 状态，停止迁移')
        if ds['DSRecordList']:
            raise ValueError('域名有 DNSSEC DS 记录，请先在阿里云移除并确认生效')
        return ns

    def _records(self, domain):
        records = []
        for page in range(1, 12):
            result = self._ali('alidns', 'DescribeDomainRecords', DomainName=domain, PageNumber=page, PageSize=100)
            rows = result.get('DomainRecords', {}).get('Record')
            total = result.get('TotalCount')
            if not isinstance(rows, list) or type(total) is not int or not 0 <= total <= 1000:
                raise ValueError('DNS 记录列表不完整或超过 1000 条，停止迁移')
            records.extend(rows)
            if not all(isinstance(row, dict) and isinstance(row.get('RecordId'), str) and row['RecordId'] for row in records) or len({row['RecordId'] for row in records}) != len(records):
                raise ValueError('DNS 记录标识不完整或重复，停止迁移')
            if len(records) >= total:
                if len(records) != total:
                    raise ValueError('DNS 记录分页不一致，请重新准备')
                return sorted(records, key=lambda r: fingerprint(r))
            if not rows:
                break
        raise ValueError('DNS 记录读取不完整，停止迁移')

    def _target_records(self, zone_id):
        records = []
        for page in range(1, 12):
            rows = self._cf('GET', f'/zones/{zone_id}/dns_records?per_page=100&page={page}')
            if not isinstance(rows, list):
                raise ValueError('Cloudflare DNS 记录列表不完整')
            records.extend(rows)
            if len(records) > 1000:
                raise ValueError('Cloudflare DNS 记录超过 1000 条')
            if len(rows) < 100:
                return sorted(records, key=lambda r: fingerprint(r))
        raise ValueError('Cloudflare DNS 记录读取不完整')

    def prepare(self, name):
        with self.lock:
            old = self._job()
            if old and old['phase'] in {'switching', 'waiting', 'uncertain'}:
                raise ValueError('先确认或完成当前迁移，不可覆盖进行中的任务')
            domain = domain_name(name)
            account = self.service.settings()['account_id']
            if not account:
                raise ValueError('请先接入 Cloudflare 账户')
            if len(self.service.settings()['zones']) >= 100:
                raise ValueError('最多接入 100 个域名')
            ns = self._source(domain)
            if not all(n.endswith(('.hichina.com', '.alidns.com', '.aliyundns.com')) for n in ns):
                raise ValueError('当前 DNS 不由阿里云托管，无法完整迁移；请使用现有域名接入方式')
            raw = self._records(domain)
            converted = [dns_record(row, domain) for row in raw]
            zones = self._cf('GET', '/zones?' + urlencode({'name': domain, 'account.id': account}))
            if not isinstance(zones, list) or len(zones) > 1:
                raise ValueError('Cloudflare 区域查询结果不明确')
            zone = zones[0] if zones else self._cf('POST', '/zones', {'name': domain, 'account': {'id': account}, 'type': 'full'})
            if not isinstance(zone, dict) or zone.get('name') != domain or zone.get('account', {}).get('id') != account or zone.get('type') != 'full':
                raise ValueError('Cloudflare 域名区域归属或类型不匹配')
            selected = Zone(zone_id=zone.get('id'), zone_name=domain).model_dump()
            target_ns = nameservers(zone.get('name_servers'))
            if not all(n.endswith('.ns.cloudflare.com') for n in target_ns):
                raise ValueError('Cloudflare DNS 服务器不符合预期')
            target = self._target_records(selected['zone_id'])
            self._compatible(converted, target)
            # Persist complete original source and target snapshots encrypted, never in audit.
            job = {'id': secrets.token_urlsafe(24), 'domain': domain, 'zone_id': selected['zone_id'], 'account_id': account,
                   'old_nameservers': ns, 'new_nameservers': target_ns, 'source': raw, 'records': converted,
                   'target': target, 'source_revision': fingerprint(raw), 'target_revision': fingerprint(target),
                   'context': self._context(), 'created_at': time.time(), 'phase': 'preview', 'task_no': '', 'message': '尚未切换 DNS，域名尚未完成接入。请核对预览，点击“确认接入并切换 DNS”继续。'}
            self._save(job)
            self.store.audit('domain_onboarding_prepared', {'domain': domain, 'records': len(converted)})
            return self.status()

    def confirm(self, identifier, confirmed_domain):
        with self.lock:
            job = self._job()
            if not job or job['id'] != identifier or domain_name(confirmed_domain) != job['domain']:
                raise ValueError('迁移确认不匹配')
            if job['phase'] in {'waiting', 'done', 'uncertain', 'switching'}:
                return self.status()
            if job['phase'] != 'preview' or time.time() - job['created_at'] > 1800:
                raise ValueError('迁移预览已失效，请重新准备')
            self._check_context(job)
            if self._source(job['domain']) != job['old_nameservers'] or fingerprint(self._records(job['domain'])) != job['source_revision'] or fingerprint(self._target_records(job['zone_id'])) != job['target_revision']:
                raise ValueError('DNS 或服务器已变化，请重新准备迁移')
            zone = self._cf('GET', '/zones/' + job['zone_id'])
            if zone.get('name') != job['domain'] or zone.get('type') != 'full' or nameservers(zone.get('name_servers')) != job['new_nameservers'] or zone.get('account', {}).get('id') != job['account_id']:
                raise ValueError('Cloudflare 区域已变化，请重新准备')
            backup_key = 'domain_backup_' + hashlib.sha256(job['domain'].encode()).hexdigest()
            if not self.store.secret(backup_key):
                self.store.set_secret(backup_key, json.dumps({'domain': job['domain'], 'at': time.time(), 'nameservers': job['old_nameservers'], 'source': job['source'], 'cloudflare': job['target']}, ensure_ascii=False))
            try:
                target = job['target']
                for row in job['records']:
                    if not any(record_identity(existing) == record_identity(row) and not existing.get('proxied', False) and existing.get('ttl') == row['ttl'] for existing in target):
                        # Identical values with different TTL/proxy settings still need review, not an overwrite.
                        if any(record_identity(existing) == record_identity(row) for existing in target):
                            raise ValueError('已有解析的 TTL 或代理状态不同，请人工核对后重新准备')
                        self._cf('POST', f'/zones/{job["zone_id"]}/dns_records', row)
                        target.append(row)
                actual = self._target_records(job['zone_id'])
                self._compatible(job['records'], actual)
                if not all(any(record_identity(e) == record_identity(r) and not e.get('proxied', False) and e.get('ttl') == r['ttl'] for e in actual) for r in job['records']):
                    raise ValueError('DNS 迁移核验失败，未切换 DNS 服务器')
                if self._source(job['domain']) != job['old_nameservers'] or fingerprint(self._records(job['domain'])) != job['source_revision']:
                    raise ValueError('迁移期间原 DNS 发生变化，未切换 DNS 服务器')
            except (ValueError, RuntimeError):
                job.update(phase='blocked', message='DNS 迁移未完成，原 DNS 服务器未修改；请核对后重新准备')
                self._save(job)
                raise
            # Persist uncertainty before submitting: never blindly retry a registry write.
            job.update(phase='switching', message='正在提交 DNS 服务器变更')
            self._save(job)
            parameters = {'AliyunDns': 'false', 'DomainName.1': job['domain']}
            parameters.update({f'DomainNameServer.{i}': ns for i, ns in enumerate(job['new_nameservers'], 1)})
            try:
                self._check_context(job)
                result = self._ali('domain', 'SaveBatchTaskForModifyingDomainDns', **parameters)
                if not isinstance(result.get('TaskNo'), str) or not result['TaskNo']:
                    raise RuntimeError('阿里云未返回任务编号')
                job.update(phase='waiting', task_no=result['TaskNo'], message='DNS 切换已提交，正在等待生效及 Cloudflare 激活。')
            except (ValueError, RuntimeError):
                job.update(phase='uncertain', message='提交结果未确认，请检测状态或到阿里云查看任务；不会自动重复提交')
                self._save(job)
                raise
            self._save(job)
            self.store.audit('domain_nameservers_submitted', {'domain': job['domain'], 'task_no': job['task_no']})
            self.resume()
            return self.status()

    def check(self):
        with self.lock:
            job = self._job()
            if not job or job['phase'] not in {'waiting', 'uncertain', 'switching'}:
                return self.status()
            self._check_context(job)
            if job.get('task_no'):
                task = self._ali('domain', 'QueryTaskDetailList', TaskNo=job['task_no'], DomainName=job['domain'], PageNum=1, PageSize=10)
                rows = task.get('Data', {}).get('TaskDetail')
                if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('DomainName') != job['domain'] or rows[0].get('TaskType') != 'CHG_DNS' or rows[0].get('TaskNo') != job['task_no']:
                    raise ValueError('无法核验阿里云变更任务')
                task_status = rows[0].get('TaskStatusCode')
                if task_status == 3:
                    job.update(phase='blocked', message='阿里云变更任务失败，请查看阿里云任务详情后重新准备')
                    self._save(job)
                    return self.status()
                if task_status not in {0, 1, 2}:
                    raise ValueError('阿里云任务状态不明确')
            detail = self._ali('domain', 'QueryDomainByDomainName', DomainName=job['domain'])
            if detail.get('DomainName') != job['domain']:
                raise ValueError('阿里云返回域名不匹配')
            current_ns = nameservers(detail.get('DnsList', {}).get('Dns'))
            zone = self._cf('GET', '/zones/' + job['zone_id'])
            if zone.get('name') != job['domain'] or zone.get('account', {}).get('id') != job['account_id'] or nameservers(zone.get('name_servers')) != job['new_nameservers']:
                raise ValueError('Cloudflare 区域状态不匹配')
            self._check_context(job)
            if current_ns == job['new_nameservers'] and zone.get('status') == 'active':
                self.service.add_zone({'zone_id': job['zone_id'], 'zone_name': job['domain']}, force_write=True)
                job.update(phase='done', message='域名已激活并接入 LanBridge，可添加网站')
                self._save(job)
                self.store.audit('domain_onboarding_completed', {'domain': job['domain']})
            elif time.time() - job['created_at'] > 86400:
                job.update(phase='uncertain', message='等待超过 24 小时，请检查阿里云及 Cloudflare；可手动检测状态')
                self._save(job)
            return self.status()

    def status(self):
        with self.lock:
            job = self._job()
            public = None
            if job:
                public = {k: deepcopy(job[k]) for k in ('id', 'domain', 'zone_id', 'old_nameservers', 'new_nameservers', 'records', 'phase', 'task_no', 'message', 'created_at')}
            return {'configured': bool(self.store.secret('aliyun_oauth_profile') if self.store.get('aliyun_auth_mode')=='oauth' else self.store.secret('aliyun_access_key_id') and self.store.secret('aliyun_access_key_secret')), 'auth_mode':self.store.get('aliyun_auth_mode','manual'), 'job': public}

    def cancel(self, identifier):
        with self.lock:
            job = self._job()
            if not job or identifier != job['id']:
                raise ValueError('任务不存在')
            if job['phase'] in {'waiting', 'uncertain', 'switching'}:
                raise ValueError('已提交的 DNS 服务器变更不能从此处撤销，请到阿里云处理')
            self.store.set_secret('domain_onboarding_job', '')
            return self.status()

    def resume(self):
        with self.lock:
            job = self._job()
            if not job or job['phase'] != 'waiting' or (self.thread and self.thread.is_alive()):
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._watch, daemon=True, name='LanBridge-domain-onboarding')
            self.thread.start()

    def _watch(self):
        while not self.stop_event.wait(60):
            try:
                result = self.check()
                if not result['job'] or result['job']['phase'] != 'waiting':
                    return
            except (ValueError, RuntimeError, OSError):
                with self.lock:
                    job = self._job()
                    if not job or time.time() - job['created_at'] > 86400:
                        return
                    job['message'] = '状态检测暂未完成，将重试；也可手动检测'
                    self._save(job)

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=90)
