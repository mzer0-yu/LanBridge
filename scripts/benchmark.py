"""Bounded local benchmark using isolated instances and a synthetic loopback origin."""
import argparse
import asyncio
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from lanbridge.models import Site
from lanbridge.service import Service
from lanbridge.launcher_control import control_instance
from lanbridge.instances import windows_processes, listening_ports, process_candidate


def summary(values):
    ordered = sorted(values)
    return {'samples': len(values), 'median_ms': round(statistics.median(values), 3),
            'p95_ms': round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3)}


def process_resources(pid):
    if sys.platform != 'win32':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD), *[(name, ctypes.c_size_t) for name in ('peak_working', 'working', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]]
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]) or not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        cpu = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 10_000_000
        return {'cpu_seconds': cpu, 'working_set_mib': round(counters.working / 1024**2, 2)}
    finally:
        kernel.CloseHandle(handle)


async def proxy_load(port, sites):
    durations = []
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(trust_env=False, timeout=10, limits=httpx.Limits(max_connections=8)) as client:
        async def request(index):
            async with semaphore:
                started = time.perf_counter()
                response = await client.get(f'http://127.0.0.1:{port}/payload', headers={'Host': sites[index % len(sites)]['hostname'], 'X-Forwarded-Proto': 'https'})
                response.raise_for_status()
                assert len(response.content) == 32768
                durations.append((time.perf_counter()-started)*1000)
        started = time.perf_counter()
        await asyncio.gather(*(request(i) for i in range(160)))
        result = summary(durations)
        result.update(concurrency=8, requests_per_second=round(160/(time.perf_counter()-started), 2))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True, help='Fresh isolated directory; never use formal data/')
    args = parser.parse_args()
    work = args.output_dir.resolve()
    # Require a fresh child of the ignored artifact directory, never a runtime path.
    work.relative_to((ROOT / '.test-artifacts').resolve())
    work.mkdir(parents=True, exist_ok=False)
    class Origin(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args): pass
        def do_GET(self):
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.send_response(200); self.send_header('Content-Length', '32768'); self.end_headers(); self.wfile.write(b'x'*32768)
    origin = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    thread = threading.Thread(target=origin.serve_forever, daemon=True); thread.start()
    report = {'scope': 'synthetic loopback only, warm requests, no Cloudflare, no production data', 'cases': []}
    try:
        for count in (1, 10, 100):
            data = work / f'data-{count}'
            service = Service(data)
            listeners = [socket.socket(), socket.socket()]
            for listener in listeners: listener.bind(('127.0.0.1', 0))
            admin, gateway = [listener.getsockname()[1] for listener in listeners]
            for listener in listeners: listener.close()
            sites = [Site(name=f'Benchmark {i}', hostname=f'bench-{i}.example.com', origin=f'http://127.0.0.1:{origin.server_port}', human_check=False, rate_limit_enabled=False).model_dump() for i in range(count)]
            service.store.set_many({'settings': service.settings() | {'admin_port': admin, 'gateway_port': gateway}, 'sites': sites, 'connector_auto_start': False})
            service.store.db.close()
            result_file = work / f'start-{count}.json'
            started = time.perf_counter()
            process = subprocess.Popen([sys.executable, str(ROOT/'run.py'), '--data-dir', str(data), 'serve', '--startup-result', str(result_file)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic()+20
                while not result_file.exists():
                    if process.poll() is not None or time.monotonic()>deadline: raise RuntimeError('Isolated startup not confirmed')
                    time.sleep(.01)
                if not json.loads(result_file.read_text(encoding='utf-8'))['ok']: raise RuntimeError('Isolated startup failed')
                startup_ms = round((time.perf_counter()-started)*1000, 2)
                with httpx.Client(trust_env=False, timeout=10, headers={'Origin': f'http://127.0.0.1:{admin}'}) as client:
                    credentials = {'username': 'benchmark', 'password': secrets.token_urlsafe(32)}
                    response = client.post(f'http://127.0.0.1:{admin}/api/setup', json=credentials); response.raise_for_status()
                    response = client.post(f'http://127.0.0.1:{admin}/api/login', json=credentials); response.raise_for_status()
                    response = client.get(f'http://127.0.0.1:{admin}/api/state'); response.raise_for_status()
                    state_times=[]
                    for _ in range(30):
                        before=time.perf_counter(); response=client.get(f'http://127.0.0.1:{admin}/api/state'); response.raise_for_status(); state_times.append((time.perf_counter()-before)*1000)
                # Windows venv python.exe can be a launcher stub. Measure the child
                # owning BOTH isolated listeners, not that stub's small working set.
                resource_pid = None
                if sys.platform == 'win32':
                    listeners_by_pid = listening_ports()
                    candidates = [process_candidate(record) for record in windows_processes()]
                    matches = [item['pid'] for item in candidates if item and Path(item['data']) == data and {admin, gateway} <= set(listeners_by_pid.get(item['pid'], []))]
                    if len(matches) == 1: resource_pid = matches[0]
                direct = asyncio.run(proxy_load(origin.server_port, sites))
                before = process_resources(resource_pid) if resource_pid else None
                load = asyncio.run(proxy_load(gateway, sites))
                after = process_resources(resource_pid) if resource_pid else None
                report['cases'].append({'sites': count, 'startup_ms': startup_ms, 'state': summary(state_times), 'direct_origin': direct, 'proxy': load, 'resources_before': before, 'resources_after': after})
            finally:
                if process.poll() is None:
                    try:
                        info=json.loads((data/'runtime.json').read_text(encoding='utf-8')); control_instance(data, info['instance'], 'stop', timeout=15)
                    except (OSError, ValueError, KeyError):
                        process.terminate()  # Only this explicitly-created isolated child.
                    process.wait(timeout=20)
        (work/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        origin.shutdown(); origin.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    main()
