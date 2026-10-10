"""Small in-memory detector for sustained abnormal traffic to paused sites."""
from collections import OrderedDict
import threading
import time


class PausedTraffic:
    WINDOW = 60
    MIN_REQUESTS = 600
    MIN_LIMITED = 300
    MIN_SCANS = 30
    LIMIT = 256

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.rows = OrderedDict()

    def observe(self, site, *, limited=False, scan=False):
        with self.lock:
            key, now = site['id'], self.clock()
            if not site.get('enabled') or not site.get('paused'):
                self.rows.pop(key, None)
                return None
            row = self.rows.get(key)
            if row is None or row['version'] != site['policy_version']:
                row = {'version': site['policy_version'], 'start': now, 'bucket': 0,
                       'requests': 0, 'limited': 0, 'scans': 0, 'streak': [], 'fired': False, 'retry_at': 0}
                self.rows[key] = row
            self.rows.move_to_end(key)
            while len(self.rows) > self.LIMIT:
                self.rows.popitem(last=False)
            bucket = int((now - row['start']) // self.WINDOW)
            if bucket != row['bucket']:
                qualifies = (bucket == row['bucket'] + 1 and row['requests'] >= self.MIN_REQUESTS
                             and (row['limited'] >= self.MIN_LIMITED or row['scans'] >= self.MIN_SCANS))
                row['streak'] = (row['streak'] + [{k: row[k] for k in ('requests', 'limited', 'scans')}])[-2:] if qualifies else []
                row.update(bucket=bucket, requests=0, limited=0, scans=0)
            row['requests'] += 1
            row['limited'] += bool(limited)
            row['scans'] += bool(scan)
            if not row['fired'] and now >= row['retry_at'] and len(row['streak']) == 2:
                row['fired'] = True
                return {'minutes': 2, **{k: sum(x[k] for x in row['streak']) for k in ('requests', 'limited', 'scans')}}
            return None

    def defer(self, site):
        with self.lock:
            row = self.rows.get(site['id'])
            if row and row['version'] == site['policy_version']:
                row.update(fired=False, retry_at=self.clock() + self.WINDOW)
