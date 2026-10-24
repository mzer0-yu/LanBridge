"""Classify DNS conflicts and authorize narrowly scoped tunnel migrations."""
from copy import deepcopy
import hashlib
import json
import re


class DnsBackupCapacityError(ValueError):
    pass


class DnsConflictError(ValueError):
    def __init__(self, conflict):
        self.conflict = conflict
        super().__init__(conflict['message'])


def inspect_conflict(cf, site, zone, records):
    cfg = cf.service.settings()
    target = cfg['tunnel_id'] + '.cfargotunnel.com'
    snapshot = {'account_id': cfg['account_id'], 'tunnel_id': cfg['tunnel_id'],
                'site_id': site['id'], 'hostname': site['hostname'], 'zone_id': zone['zone_id'],
                'policy_version': site.get('policy_version'),
                'records_digest': hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()}
    revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = {'revision': revision, 'hostname': site['hostname'], 'repairable': False,
              'snapshot': snapshot, 'target': target,
              'message': site['hostname'] + ' 已有冲突 DNS，平台不会覆盖',
              'detail': '现有记录无法自动接管，请核对域名用途或改用其他公网域名。'}
    if len(records) != 1:
        return result
    record = records[0]
    match = re.fullmatch(r'([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\.cfargotunnel\.com', str(record.get('content', '')).lower().rstrip('.'))
    if (record.get('type') != 'CNAME' or not record.get('proxied') or not match
            or match[1] == cfg['tunnel_id'] or record.get('name', '').lower().rstrip('.') != site['hostname']):
        return result
    try:
        old = cf.request('GET', f"/accounts/{cfg['account_id']}/cfd_tunnel/{match[1]}")
    except (ValueError, RuntimeError):
        result['detail'] = '域名指向另一条隧道，但无法核实其归属及运行状态；暂不提供迁移。'
        return result
    if not isinstance(old, dict) or old.get('id') != match[1] or old.get('deleted_at'):
        return result
    name = str(old.get('name') or '另一条隧道')[:120]
    result.update(old_tunnel=name, old_status=str(old.get('status') or 'unknown'))
    result['repairable'] = old.get('status') == 'down' and old.get('connections') == []
    if result['repairable']:
        result['message'] = site['hostname'] + ' DNS 仍指向已离线的旧隧道，可迁移到 LanBridge'
    result['detail'] = (f'域名仍指向同账户隧道“{name}”（已离线）。迁移将备份原记录并切换到 LanBridge，原入口将被替换。'
                        if result['repairable'] else f'域名指向同账户隧道“{name}”（尚未确认离线）。为避免影响原服务，不自动接管。')
    return result


def accepted_conflict(current, approved):
    candidates = approved if isinstance(approved, list) else [approved]
    return bool(current['repairable'] and any(candidate and candidate.get('revision') == current['revision'] for candidate in candidates))


def migrate_record(cf, item, approved):
    service = cf.service
    if isinstance(approved, list):
        approved = next(candidate for candidate in approved if candidate['hostname'] == item['hostname'])
    snapshot = approved['snapshot']
    site = next((s for s in service.sites() if s['id'] == snapshot['site_id'] and s['enabled']), None)
    if not site:
        raise ValueError('网站配置已经变化，请重新检查 DNS 冲突')
    zone = service.site_zone(site)
    records = cf.request('GET', f"/zones/{zone['zone_id']}/dns_records?name={site['hostname']}")
    current = inspect_conflict(cf, site, zone, records)
    if not accepted_conflict(current, approved):
        raise DnsConflictError(current)
    record = records[0]
    backup = {'revision': approved['revision'], 'hostname': site['hostname'],
              'zone_id': zone['zone_id'], 'record': deepcopy(record), 'target': item['target'], 'status': 'prepared'}
    history = service.store.get('dns_migration_backups', [])
    history = [r for r in history if r.get('revision') != backup['revision']]
    while len(history) >= 100:
        verified = next((i for i, row in enumerate(history) if row.get('status') == 'verified'), None)
        if verified is None:
            raise DnsBackupCapacityError('DNS 迁移备份已满，仍有待核对记录；请先核对历史迁移结果')
        del history[verified]
    history.append(backup)
    service.store.set('dns_migration_backups', history)
    # Update the existing record only after its backup is durably saved. Keep TTL/tags/comment/settings.
    body = {key: record[key] for key in ('type', 'name', 'ttl', 'proxied', 'comment', 'tags', 'settings') if key in record}
    body.update(content=item['target'], proxied=True)
    try:
        cf.request('PATCH', f"/zones/{zone['zone_id']}/dns_records/{record['id']}", body)
        actual = cf.request('GET', f"/zones/{zone['zone_id']}/dns_records?name={site['hostname']}")
        if len(actual) != 1 or actual[0].get('type') != 'CNAME' or actual[0].get('content', '').rstrip('.') != item['target'] or not actual[0].get('proxied'):
            raise RuntimeError('DNS 迁移写后核验失败')
        backup['status'] = 'verified'
        service.store.set('dns_migration_backups', history)
        service.store.audit('dns_migration_verified', {'hostname': site['hostname']})
    except Exception:
        # A timeout can commit remotely. Preserve the backup and reconcile on normal publication retry.
        backup['status'] = 'needs_recheck'
        service.store.set('dns_migration_backups', history)
        raise


def reconcile_migration_backups(service, verified_dns):
    """Resolve previously unknown writes only after route and DNS readback succeeded."""
    history = service.store.get('dns_migration_backups', [])
    changed = False
    confirmed = {(item['hostname'], item['zone_id'], item['target'], rows[0].get('id'))
                 for item, rows in verified_dns
                 if len(rows) == 1 and rows[0].get('type') == 'CNAME'
                 and rows[0].get('content', '').rstrip('.') == item['target'] and rows[0].get('proxied')}
    for backup in history:
        if (backup.get('status') in {'prepared', 'needs_recheck'}
                and (backup.get('hostname'), backup.get('zone_id'), backup.get('target'), (backup.get('record') or {}).get('id')) in confirmed):
            backup['status'] = 'verified'
            changed = True
    if changed:
        service.store.set('dns_migration_backups', history)
