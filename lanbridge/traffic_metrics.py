"""Rolling page views and bounded, pseudonymous distinct source IP counts."""
import hashlib
import secrets


class TrafficMetrics:
    WINDOW = 1440
    IP_LIMIT = 10000

    def __init__(self, store, clock):
        self.clock = clock
        self.pages, self.ips, self.overflow = {}, {}, {}
        self.dirty = False
        saved = store.get('visitor_traffic_metrics', {})
        if not isinstance(saved, dict):
            saved = {}
            self.dirty = True
        salt = saved.get('salt', '')
        try:
            self.salt = bytes.fromhex(salt) if isinstance(salt, str) and len(salt) == 64 else secrets.token_bytes(32)
        except ValueError:
            self.salt = secrets.token_bytes(32)
        salt_valid = isinstance(salt, str) and salt == self.salt.hex()
        if saved and not salt_valid:
            self.dirty = True
        active = {s['id'] for s in store.get('sites', [])}
        for field, target, size in [('pages', self.pages, 3), ('ips', self.ips, 3), ('overflow', self.overflow, 2)]:
            rows = saved.get(field, [])
            if not isinstance(rows, list):
                rows = []
                self.dirty = True
            for row in rows:
                if not isinstance(row, list) or len(row) != size or not isinstance(row[0], str) or row[0] not in active:
                    self.dirty = True
                    continue
                site = row[0]
                if field == 'pages' and type(row[1]) is int and type(row[2]) is int and row[2] >= 0:
                    target.setdefault(site, {})[row[1]] = row[2]
                elif field == 'ips' and salt_valid and isinstance(row[1], str) and len(row[1]) == 32 and all(c in '0123456789abcdef' for c in row[1]) and type(row[2]) is int:
                    peers = target.setdefault(site, {})
                    if len(peers) < self.IP_LIMIT:
                        peers[row[1]] = row[2]
                    else:
                        self.overflow[site] = int(clock() // 60)
                        self.dirty = True
                elif field == 'overflow' and type(row[1]) is int:
                    target[site] = row[1]
                else:
                    self.dirty = True
        self.minute = None
        self.prune()

    def prune(self, active=None):
        minute = int(self.clock() // 60)
        if minute == self.minute and active is None:
            return minute
        self.minute = minute
        oldest = minute - self.WINDOW + 1
        for field in (self.pages, self.ips):
            for site, bucket in list(field.items()):
                kept = {k: v for k, v in bucket.items() if (oldest <= (k if field is self.pages else v) <= minute) and (active is None or site in active)}
                if len(kept) != len(bucket):
                    self.dirty = True
                if kept:
                    field[site] = kept
                else:
                    del field[site]
        for site, timestamp in list(self.overflow.items()):
            if not oldest <= timestamp <= minute or (active is not None and site not in active):
                del self.overflow[site]
                self.dirty = True
        return minute

    def record(self, site, ip):
        minute = self.prune()
        pages = self.pages.setdefault(site, {})
        pages[minute] = pages.get(minute, 0) + 1
        digest = hashlib.blake2b(ip.encode(), key=self.salt, digest_size=16).hexdigest()
        peers = self.ips.setdefault(site, {})
        if digest in peers or len(peers) < self.IP_LIMIT:
            peers[digest] = minute
        else:
            self.overflow[site] = minute
        self.dirty = True

    def snapshot(self, site):
        self.prune()
        views = sum(self.pages.get(site, {}).values())
        return {'page_views': views, 'average_hourly_views': round(views / 24, 2),
                'unique_ips': len(self.ips.get(site, {})), 'unique_ips_limited': site in self.overflow}

    def export(self):
        return {'salt': self.salt.hex(),
                'pages': [[s, m, v] for s, rows in self.pages.items() for m, v in rows.items()],
                'ips': [[s, ip, m] for s, rows in self.ips.items() for ip, m in rows.items()],
                'overflow': [[s, m] for s, m in self.overflow.items()]}
