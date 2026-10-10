import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from lanbridge.visitor_risk import VisitorRisk
from lanbridge.gateway import create_gateway
from test_visitor_risk import MemoryStore
from test_security import service


def test_pages_ips_rolling_restart_and_no_raw_ips():
    store = MemoryStore(); store.values['sites'] = [{'id':'one'}, {'id':'two'}]
    clock = [100000.0]; risk = VisitorRisk(store, clock=lambda:clock[0])
    for ip in ['203.0.113.1', '203.0.113.1', '203.0.113.2']:
        risk.record_page_view('one', ip)
    risk.record_page_view('two', '203.0.113.1')
    one = risk.snapshot(['one', 'two'])['sites']['one']
    assert (one['page_views'], one['unique_ips'], one['average_hourly_views']) == (3, 2, 0.12)
    risk.count('verified', 'one'); risk.flush_metrics(); assert store.writes == 1
    assert '203.0.113.' not in json.dumps(store.values)
    reloaded = VisitorRisk(store, clock=lambda:clock[0])
    reloaded.record_page_view('one', '203.0.113.1')
    assert reloaded.snapshot(['one'])['sites']['one']['unique_ips'] == 2
    clock[0] += 86460
    assert reloaded.snapshot(['one'])['sites']['one']['page_views'] == 0
    reloaded.flush_metrics()
    assert store.get('visitor_traffic_metrics')['ips'] == []


def test_ip_last_seen_expiration_capacity_and_deleted_site():
    store = MemoryStore();store.values['sites'] = [{'id':'one'}]
    clock = [100000.0];risk = VisitorRisk(store, clock=lambda:clock[0]);risk.traffic.IP_LIMIT = 2
    risk.record_page_view('one', 'a');risk.record_page_view('one', 'b');risk.record_page_view('one', 'c')
    assert risk.snapshot(['one'])['sites']['one']['unique_ips_limited']
    clock[0] += 86340; risk.record_page_view('one', 'a');clock[0] += 120
    row = risk.snapshot(['one'])['sites']['one']
    assert row['unique_ips'] == 1 and row['page_views'] == 1 and not row['unique_ips_limited']
    store.values['sites'] = [];risk.flush_metrics()
    assert store.get('visitor_traffic_metrics')['pages'] == []


def test_traffic_failed_checkpoint_retries(service, monkeypatch):
    site = service.save_site({'name':'traffic','hostname':'app.example.com','origin':'http://127.0.0.1:9400','human_check':False})
    risk = service.visitor_risk;risk.record_page_view(site['id'], '203.0.113.1');risk.count('verified', site['id'])
    original = service.store.set_many
    def fail(values): raise OSError('test save failure')
    monkeypatch.setattr(service.store, 'set_many', fail)
    with pytest.raises(OSError):risk.flush_metrics()
    assert risk.traffic.dirty
    monkeypatch.setattr(service.store, 'set_many', original);risk.flush_metrics()
    assert VisitorRisk(service.store).snapshot([site['id']])['sites'][site['id']]['page_views'] == 1


@pytest.mark.parametrize('saved',[None, {}, {'pages':None,'ips':[['one','a'*32,1666]],'salt':'z'*64}, {'pages':[['one','bad',1]],'ips':[['gone','a'*32,123]]}])
def test_invalid_traffic_storage_is_safe(saved):
    store = MemoryStore();store.values.update(sites=[{'id':'one'}],visitor_traffic_metrics=saved)
    risk = VisitorRisk(store, clock=lambda:100000)
    assert risk.snapshot(['one'])['sites']['one']['page_views'] == 0
    assert risk.snapshot(['one'])['sites']['one']['unique_ips'] == 0
    risk.record_page_view('one','test');risk.flush_metrics()
    assert VisitorRisk(store,clock=lambda:100000).snapshot(['one'])['sites']['one']['unique_ips'] == 1


def test_gateway_counts_only_completed_successful_pages(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(404 if self.path == '/missing' else 200)
            self.send_header('Content-Type', 'application/json' if self.path == '/api' else 'text/html')
            body=b'<html>test</html>';self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        site=service.save_site({'name':'traffic','hostname':'app.example.com','origin':f'http://127.0.0.1:{server.server_port}','human_check':False})
        with TestClient(create_gateway(service),base_url='https://app.example.com',client=('127.0.0.1',1)) as client:
            headers={'Accept':'text/html','CF-Connecting-IP':'203.0.113.1'}
            for path in ['/', '/', '/file.css', '/api', '/missing']:client.get(path,headers=headers)
            client.get('/',headers=headers|{'CF-Connecting-IP':'203.0.113.2'})
            row=service.visitor_risk.snapshot([site['id']])['sites'][site['id']]
            assert (row['page_views'],row['unique_ips']) == (3,2)
            service.save_site(site|{'paused':True});client.get('/',headers=headers)
            assert service.visitor_risk.snapshot([site['id']])['sites'][site['id']]['page_views'] == 3
            service.save_site(site|{'human_check':True});client.get('/',headers=headers)
            assert service.visitor_risk.snapshot([site['id']])['sites'][site['id']]['page_views'] == 3
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
