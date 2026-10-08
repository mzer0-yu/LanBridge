"""Bounded visitor abuse signals, cooldowns and persistent grant revocation.

Only escalation writes storage; normal traffic reads small in-memory indexes.
Raw cookies, IP addresses and paths are never persisted here.
"""
from collections import OrderedDict
import hashlib
import math
import threading
import time
from urllib.parse import unquote


class VisitorRisk:
    LIMIT = 4096
    HORIZON = 30 * 86400
    RULES = {'rate': (60, 60), 'scan': (60, 8), 'verify': (300, 5), 'flood': (60, 1)}

    def __init__(self, store, *, clock=None):
        self.store, self.clock = store, clock or (lambda: time.time())
        self.lock = threading.RLock()
        self.events = OrderedDict()
        self.started = self.clock()
        self.metrics = {'verified': 0, 'memory_hits': 0, 'restricted': 0}
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
            self.metrics['restricted'] += 1
            self.store.audit('visitor_restricted', {'site_id': site_id, 'reason': kind,
                                                   'seconds': seconds, 'grants': len(grants)})
            return seconds

    def count(self, kind):
        with self.lock:
            self.metrics[kind] += 1

    def snapshot(self):
        with self.lock:
            return {'since': self.started, **self.metrics}
