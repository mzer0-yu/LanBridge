"""Read-only OSV lookup for pinned Python dependencies; no credentials are sent."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main():
    project = Path(__file__).resolve().parents[1]
    packages = []
    for line in (project / 'requirements.lock.txt').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name, version = line.strip().split('==', 1)
        packages.append({'name': name, 'version': version})
    results = [[] for _ in packages]
    pending = list(enumerate({'package': {'name': p['name'], 'ecosystem': 'PyPI'},
                             'version': p['version']} for p in packages))
    with httpx.Client(timeout=30, trust_env=False) as client:
        while pending:
            response = client.post('https://api.osv.dev/v1/querybatch',
                                   json={'queries': [query for _, query in pending]})
            response.raise_for_status()
            entries = response.json()['results']
            if len(entries) != len(pending):
                raise RuntimeError('OSV response count does not match queries')
            following = []
            for (index, query), entry in zip(pending, entries):
                results[index].extend(entry.get('vulns', []))
                if entry.get('next_page_token'):
                    following.append((index, query | {'page_token': entry['next_page_token']}))
            pending = following
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'OSV',
              'packages': [p | {'vulnerabilities': result} for p, result in zip(packages, results)]}
    target = project / '.test-artifacts/dependencies.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    hits = [p for p in report['packages'] if p['vulnerabilities']]
    print(json.dumps({'packages_checked':len(packages), 'affected_packages':hits}, ensure_ascii=False))
    return bool(hits)


if __name__ == '__main__':
    raise SystemExit(main())
