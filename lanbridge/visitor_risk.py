"""Bounded visitor abuse signals, cooldowns and persistent grant revocation.

Abuse escalation persists immediately; aggregate and per-site metrics checkpoint once per minute.
Raw cookies, IP addresses and paths are never persisted here.
"""
from collections import OrderedDict
import hashlib
import logging
import math
import threading
import time
from urllib.parse import unquote
from .traffic_metrics import TrafficMetrics


class VisitorRisk:
    METRIC_NAMES = ('verified', 'memory_hits', 'restricted')
    WINDOW = 86400
    BUCKET = 60
    LIMIT = 4096
    HORIZON = 30 * 86400
    RULES = {'rate': (60, 60), 'scan': (60, 8), 'verify': (300, 5), 'flood': (60, 1)}

    def __init__(self, store, *, clock=None):
        self.store, self.clock = store, clock or (lambda: time.time())
        self.lock = threading.RLock()
        self.events = OrderedDict()
        self.metric_buckets = {}
        self.site_metric_buckets = {}
        self.site_metrics_dirty = False
        self.metrics_dirty = False
        self.metrics_stopped = threading.Event()
        self.metrics_thread = None
        now = int(self.clock() // self.BUCKET)
        self.metric_minute = now
        self.traffic = TrafficMetrics(store, self.clock)
        saved_metrics = store.get('visitor_metrics', [])
        if not isinstance(saved_metrics, list):
            saved_metrics = []
            self.metrics_dirty = True
        for row in saved_metrics:
            if (isinstance(row, list) and len(row) == 4
                    and all(type(value) is int and value >= 0 for value in row)
                    and now - self.WINDOW // self.BUCKET + 1 <= row[0] <= now):
                self.metric_buckets[row[0]] = row[1:]
            else:
                self.metrics_dirty = True
        saved_sites = store.get('visitor_site_metrics', [])
        active_ids = {site['id'] for site in store.get('sites', [])}
        if not isinstance(saved_sites, list):
            saved_sites = []
            self.site_metrics_dirty = True
        for row in saved_sites:
            if (isinstance(row, list) and len(row) == 5
                    and isinstance(row[1], str) and row[1] in active_ids
                    and all(type(value) is int and value >= 0 for value in [row[0], *row[2:]])
                    and now - self.WINDOW // self.BUCKET + 1 <= row[0] <= now):
                self.site_metric_buckets.setdefault(row[0], {})[row[1]] = row[2:]
            else:
                self.site_metrics_dirty = True
        state = store.get('visitor_risk', {})
        self.state = {key: dict(state.get(key, {})) for key in ('blocked', 'revoked', 'epochs', 'site_blocked', 'verify_blocked')}
        self._prune(self.clock())

    @staticmethod
    def _peer(site_id, ip):
        return hashlib.sha256((site_id + ':' + ip).encode()).hexdigest()

    def _prune(self, now):
        for key, field in [('blocked', 'retain_until'), ('epochs', 'expires')]:
            self.state[key] = {name: row for name, row in self.state[key].items() if row[field] > now}
        self.state['site_blocked'] = {key: until for key, until in self.state['site_blocked'].items() if until > now}
        self.state['verify_blocked'] = {key: until for key, until in self.state['verify_blocked'].items() if until > now}
        self.state['revoked'] = {key: expires for key, expires in self.state['revoked'].items() if expires > now}

    def remaining(self, site_id, ip, browser_id=None, *, verify=False):
        with self.lock:
            row = self.state['blocked'].get(self._peer(site_id, ip), {})
            browser = self.state['blocked'].get(self._peer(site_id, 'browser:' + browser_id), {}) if browser_id else {}
            return max(0, math.ceil(max(row.get('until', 0), browser.get('until', 0), self.state['site_blocked'].get(site_id, 0), self.state['verify_blocked'].get(self._peer(site_id, ip), 0) if verify else 0) - self.clock()))

    def revoked(self, site_id, token, issued, ip=None, *, source=None, browser_id=None):
        with self.lock:
            now = self.clock()
            epoch = self.state['epochs'].get(site_id, {})
            peer_key = self._peer(site_id, ip) if ip is not None else source
            peer = self.state['blocked'].get(peer_key, {}) if peer_key else {}
            browser = self.state['blocked'].get(self._peer(site_id, 'browser:' + browser_id), {}) if browser_id else {}
            return ((browser.get('retain_until', 0) > now and issued <= browser.get('revoke_before', 0))
                    or (peer.get('retain_until', 0) > now and issued <= peer.get('revoke_before', 0))
                    or self.state['revoked'].get(hashlib.sha256(token.encode()).hexdigest(), 0) > now
                    or (epoch.get('expires', 0) > now and issued <= epoch.get('at', 0)))

    @staticmethod
    def sensitive_path(path):
        path = unquote(path[:512]).lower().replace('\\', '/')
        return (path.startswith(('/.env', '/.git/', '/.svn/', '/.aws/', '/.ssh/'))
                or path in {'/wp-config.php', '/wp-config.php.bak', '/config.php.bak',
                            '/etc/passwd', '/server-status', '/phpinfo.php', '/backup.sql', '/database.sql'})

    def record(self, site_id, ip, kind, *, path='', grants=(), browser_id=None):
        window, threshold = self.RULES[kind]
        with self.lock:
            now = self.clock()
            peer = self._peer(site_id, 'browser:' + browser_id if browser_id else ip)
            row = self.state['blocked'].get(peer, {})
            until = max(row.get('until', 0), self.state['site_blocked'].get(site_id, 0))
            if until > now:
                return math.ceil(until - now)
            key = (peer, kind)
            event = self.events.get(key)
            if event is None or now - event['since'] >= window:
                event = {'since': now, 'count': 0, 'paths': set()}
                self.events[key] = event
            self.events.move_to_end(key)
            while len(self.events) > self.LIMIT:
                self.events.popitem(last=False)
            if kind == 'scan':
                if not self.sensitive_path(path):
                    return 0
                event['paths'].add(unquote(path[:512]).lower())
                event['count'] = len(event['paths'])
            else:
                event['count'] += 1
            if event['count'] < threshold:
                return 0
            self._prune(now)
            previous = self.state['blocked'].get(peer, {})
            strikes = min(3, (previous.get('strikes', 0) if previous.get('revoke_before', 0) >= now - 86400 else 0) + 1)
            seconds = 300 * strikes
            # Capacity exhaustion fails closed by revoking older grants for this site.
            if peer not in self.state['blocked'] and len(self.state['blocked']) >= self.LIMIT:
                self.state['site_blocked'][site_id] = now + seconds
                self.state['epochs'][site_id] = {'at': now, 'expires': now + self.HORIZON}
            else:
                self.state['blocked'][peer] = {'until': now + seconds, 'retain_until': now + self.HORIZON, 'strikes': strikes, 'revoke_before': now}
            if browser_id:
                network = self._peer(site_id, ip)
                if network not in self.state['verify_blocked'] and len(self.state['verify_blocked']) >= self.LIMIT:
                    self.state['site_blocked'][site_id] = now + seconds
                else:
                    self.state['verify_blocked'][network] = max(self.state['verify_blocked'].get(network, 0), now + seconds)
            for token, expires in grants:
                fingerprint = hashlib.sha256(token.encode()).hexdigest()
                if fingerprint not in self.state['revoked'] and len(self.state['revoked']) >= self.LIMIT:
                    self.state['epochs'][site_id] = {'at': now, 'expires': now + self.HORIZON}
                    break
                self.state['revoked'][fingerprint] = min(expires, now + self.HORIZON)
            self.events.pop(key, None)
            self.store.set('visitor_risk', self.state)
            self.count('restricted', site_id)
            self.store.audit('visitor_restricted', {'site_id': site_id, 'reason': kind,
                                                   'seconds': seconds, 'grants': len(grants)})
            return seconds

    def _prune_metrics(self, now):
        minute = int(now // self.BUCKET)
        if minute == self.metric_minute:
            return minute
        self.metric_minute = minute
        oldest = minute - self.WINDOW // self.BUCKET + 1
        for buckets, dirty in [(self.metric_buckets, 'metrics_dirty'), (self.site_metric_buckets, 'site_metrics_dirty')]:
            stale = [key for key in buckets if key < oldest or key > minute]
            for key in stale:
                del buckets[key]
            if stale:
                setattr(self, dirty, True)
        return minute

    def count(self, kind, site_id=None):
        index = self.METRIC_NAMES.index(kind)
        with self.lock:
            minute = self._prune_metrics(self.clock())
            self.metric_buckets.setdefault(minute, [0, 0, 0])[index] += 1
            self.metrics_dirty = True
            if site_id is not None:
                self.site_metric_buckets.setdefault(minute, {}).setdefault(site_id, [0, 0, 0])[index] += 1
                self.site_metrics_dirty = True

    def record_page_view(self, site_id, ip):
        with self.lock:
            self.traffic.record(site_id, ip)

    def snapshot(self, site_ids=None):
        with self.lock:
            now = self.clock()
            self._prune_metrics(now)
            totals = [sum(row[index] for row in self.metric_buckets.values()) for index in range(3)]
            result = {'since': now - self.WINDOW, 'window_seconds': self.WINDOW,
                      'bucket_seconds': self.BUCKET, **dict(zip(self.METRIC_NAMES, totals))}
            if site_ids is not None:
                per_site = {site_id: [0, 0, 0] for site_id in site_ids}
                for bucket in self.site_metric_buckets.values():
                    for site_id, values in bucket.items():
                        if site_id in per_site:
                            per_site[site_id] = [a + b for a, b in zip(per_site[site_id], values)]
                result['sites'] = {site_id: dict(zip(self.METRIC_NAMES, values)) | self.traffic.snapshot(site_id) for site_id, values in per_site.items()}
                result['unattributed'] = dict(zip(self.METRIC_NAMES, [max(0, total - sum(values[i] for values in per_site.values())) for i, total in enumerate(totals)]))
            return result

    def flush_metrics(self):
        with self.lock:
            self._prune_metrics(self.clock())
            active_ids = {site['id'] for site in self.store.get('sites', [])}
            for minute, bucket in list(self.site_metric_buckets.items()):
                obsolete = set(bucket) - active_ids
                for site_id in obsolete:
                    del bucket[site_id]
                if obsolete:
                    self.site_metrics_dirty = True
                if not bucket:
                    del self.site_metric_buckets[minute]
            self.traffic.prune(active_ids)
            values = {}
            if self.traffic.dirty:
                values['visitor_traffic_metrics'] = self.traffic.export()
            if self.metrics_dirty:
                values['visitor_metrics'] = [[minute, *row] for minute, row in sorted(self.metric_buckets.items())]
            if self.site_metrics_dirty:
                values['visitor_site_metrics'] = [[minute, site_id, *row] for minute, bucket in sorted(self.site_metric_buckets.items()) for site_id, row in sorted(bucket.items())]
            if len(values) >= 2:
                self.store.set_many(values)
            elif values:
                self.store.set(*next(iter(values.items())))
            self.metrics_dirty = self.site_metrics_dirty = self.traffic.dirty = False

    def start_metrics(self):
        if self.metrics_thread is not None:
            return
        def checkpoint():
            while not self.metrics_stopped.wait(self.BUCKET):
                try:
                    self.flush_metrics()
                except Exception:
                    logging.getLogger(__name__).exception('visitor_metrics_checkpoint_failed')
        self.metrics_thread = threading.Thread(target=checkpoint, name='lanbridge-visitor-metrics', daemon=True)
        self.metrics_thread.start()

    def stop_metrics(self):
        self.metrics_stopped.set()
        if self.metrics_thread is not None:
            self.metrics_thread.join()
        self.flush_metrics()
