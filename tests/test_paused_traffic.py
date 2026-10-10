import threading
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from lanbridge.paused_traffic import PausedTraffic
from lanbridge.site_pause import SitePause
from lanbridge.gateway import create_gateway
from test_security import service
from test_cloud_pause import setup, finish


def traffic_site(**changes):
    return {'id': 'site', 'enabled': True, 'paused': True, 'policy_version': 'one', **changes}


def fill(detector, site, count=600, **signals):
    return [result for _ in range(count) if (result := detector.observe(site, **signals))]


def test_requires_two_complete_consecutive_minutes_with_volume_and_abuse():
    now = [0.0];detector = PausedTraffic(lambda: now[0]);site = traffic_site()
    assert not fill(detector, site, limited=True)
    now[0] = 60;assert not fill(detector, site, limited=True)
    now[0] = 120;reason = detector.observe(site)
    assert reason == {'minutes': 2, 'requests': 1200, 'limited': 1200, 'scans': 0}
    assert not fill(detector, site, 6000, limited=True)


@pytest.mark.parametrize('scenario', ['volume_only', 'short_burst', 'gap', 'low_volume', 'resumed'])
def test_normal_or_incomplete_traffic_cannot_escalate(scenario):
    now=[0.0];d=PausedTraffic(lambda:now[0]);site=traffic_site()
    count=599 if scenario=='low_volume' else 600
    flags={} if scenario=='volume_only' else {'limited':True}
    assert not fill(d,site,count,**flags)
    now[0]=180 if scenario=='gap' else 60
    if scenario=='resumed':site=traffic_site(paused=False)
    assert not fill(d,site,count,**flags)
    if scenario=='short_burst':now[0]=119.9
    else:now[0]+=60
    assert d.observe(site,**flags) is None


def test_scanning_can_qualify_but_requires_high_volume_in_both_minutes():
    now=[0.0];d=PausedTraffic(lambda:now[0]);site=traffic_site()
    for minute in range(2):
        now[0]=minute*60
        assert not fill(d,site,30,scan=True)
        assert not fill(d,site,570)
    now[0]=120;assert d.observe(site)['scans']==60


def test_detector_is_bounded_and_version_change_restarts_windows():
    now=[0.0];d=PausedTraffic(lambda:now[0]);site=traffic_site()
    fill(d,site,limited=True);now[0]=60;fill(d,site,limited=True)
    now[0]=120;assert d.observe(traffic_site(policy_version='new')) is None
    for i in range(d.LIMIT+10):d.observe(traffic_site(id=str(i)))
    assert len(d.rows)==d.LIMIT


def prepared(service,monkeypatch):
    site,waf=setup(service,monkeypatch);service.store.set('published_hosts',[site['hostname']])
    return service.set_site_paused(site['id'],True),waf


REASON={'minutes':2,'requests':1200,'limited':900,'scans':0}


def test_auto_block_is_selective_persistent_once_and_manual_restore(service,monkeypatch):
    site,waf=prepared(service,monkeypatch)
    assert service.site_pause.auto_block(site['id'],site['policy_version'],REASON)
    job=finish(service);assert job['automatic'] and '已自动' in job['message']
    assert job['automatic_reason']==REASON
    original_calls=len(waf.calls)
    SitePause(service).auto_block(site['id'],site['policy_version'],REASON)
    assert len(waf.calls)==original_calls
    service.site_pause.submit(site['id'],True,False);assert finish(service)['phase']=='succeeded'
    assert service.sites()[0]['paused']
    service.site_pause.auto_block(site['id'],site['policy_version'],REASON)
    assert len(waf.ruleset['rules'])==1
    edited=service.save_site(service.sites()[0]|{'requests_per_minute':200})
    service.site_pause.auto_block(site['id'],edited['policy_version'],REASON)
    assert len(waf.ruleset['rules'])==1  # Editing a policy does not create a new pause episode.
    service.site_pause.submit(site['id'],False)
    site=service.set_site_paused(site['id'],True)
    service.site_pause.auto_block(site['id'],site['policy_version'],REASON)
    assert finish(service)['phase']=='succeeded'
    assert len(waf.ruleset['rules'])==2
    audit=[x for x in service.store.audit_list() if x['action']=='site_auto_cloud_pause_triggered']
    assert len(audit)==2


def test_failed_auto_block_keeps_pause_and_never_retries_after_restart(service,monkeypatch):
    site,waf=prepared(service,monkeypatch)
    def deny(*args,**kwargs):raise ValueError('需要 Zone WAF 编辑权限')
    monkeypatch.setattr(service.cf,'request',deny)
    service.site_pause.auto_block(site['id'],site['policy_version'],REASON)
    job=finish(service);assert job['phase']=='failed' and job['automatic']
    assert service.sites()[0]['paused']
    restarted=SitePause(service)
    monkeypatch.setattr(restarted,'submit',lambda *a,**kw:pytest.fail('Must not automatically retry'))
    assert restarted.auto_block(site['id'],site['policy_version'],REASON)


@pytest.mark.parametrize('change',['resume','version','unpublished','disabled'])
def test_queued_signal_rechecks_current_site_before_cloud_write(service,monkeypatch,change):
    site,waf=prepared(service,monkeypatch);version=site['policy_version']
    if change=='resume':service.set_site_paused(site['id'],False)
    elif change=='version':version='old'
    elif change=='unpublished':service.store.set('published_hosts',[])
    else:service.store.set('sites',[site|{'enabled':False}])
    assert service.site_pause.auto_block(site['id'],version,REASON)
    assert not waf.calls and not service.store.get('paused_auto_attempts')


def test_auto_job_does_not_wait_for_cloudflare_network(service,monkeypatch):
    site,waf=prepared(service,monkeypatch);entered=threading.Event();release=threading.Event()
    original=waf.request
    def wait(*args,**kwargs):
        entered.set();assert release.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(service.cf,'request',wait)
    try:
        assert service.site_pause.auto_block(site['id'],site['policy_version'],REASON)
        assert entered.wait(1)
        assert service.site_pause.status()[site['id']]['phase']=='running'
        assert service.site_pause.auto_block(site['id'],site['policy_version'],REASON) is False
    finally:release.set();finish(service)


def test_public_gateway_counts_limited_paused_requests_and_only_trusted_connector(service,monkeypatch):
    site,waf=prepared(service,monkeypatch);now=[0.0]
    detector=service.site_pause.traffic;detector.clock=lambda:now[0];detector.MIN_REQUESTS=15;detector.MIN_LIMITED=5
    site=service.save_site(site|{'requests_per_minute':10})
    client=TestClient(create_gateway(service),base_url='https://app.example.com',client=('127.0.0.1',123))
    headers={'CF-Connecting-IP':'203.0.113.4'}
    for minute in range(2):
        now[0]=minute*60
        for _ in range(20):assert client.get('/',headers=headers).status_code in (503,429)
    now[0]=120;client.get('/',headers=headers)
    assert finish(service)['automatic'];assert len(waf.ruleset['rules'])==2
    assert client.get('/',headers=headers).status_code in (503,429)


@pytest.mark.parametrize('peer,headers',[('198.51.100.8',{'CF-Connecting-IP':'203.0.113.4'}),('127.0.0.1',{})])
def test_untrusted_or_local_requests_do_not_feed_detector(service,monkeypatch,peer,headers):
    site,waf=prepared(service,monkeypatch)
    client=TestClient(create_gateway(service),base_url='https://app.example.com',client=(peer,123))
    client.get('/.env',headers=headers)
    assert not service.site_pause.traffic.rows and not waf.calls


def test_paused_websocket_handshakes_count_but_never_open_upstream(service,monkeypatch):
    site,waf=prepared(service,monkeypatch)
    client=TestClient(create_gateway(service),base_url='https://app.example.com',client=('127.0.0.1',123))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('wss://app.example.com/.env',headers={'CF-Connecting-IP':'203.0.113.4','X-Forwarded-Proto':'https'}):pass
    row=service.site_pause.traffic.rows[site['id']]
    assert row['requests']==1 and row['scans']==1
    assert not waf.calls


def test_gateway_admission_rejections_also_trigger_paused_auto_block(service,monkeypatch):
    from lanbridge.gateway import ResourceLimits
    site,waf=prepared(service,monkeypatch);now=[0.0];detector=service.site_pause.traffic
    detector.clock=lambda:now[0];detector.MIN_REQUESTS=3;detector.MIN_LIMITED=3
    app=create_gateway(service)
    limits=next(m for m in app.user_middleware if m.cls is ResourceLimits)
    limits.kwargs.update(rate=0,burst=0)
    client=TestClient(app,base_url='https://app.example.com',client=('127.0.0.1',123))
    headers={'CF-Connecting-IP':'203.0.113.4'}
    for minute in range(2):
        now[0]=minute*60
        for _ in range(3):assert client.get('/',headers=headers).status_code==429
    now[0]=120;assert client.get('/',headers=headers).status_code==429
    assert finish(service)['automatic']


def test_gateway_busy_rejections_feed_paused_detector(service,monkeypatch):
    from lanbridge.gateway import ResourceLimits
    site,waf=prepared(service,monkeypatch);app=create_gateway(service)
    limits=next(m for m in app.user_middleware if m.cls is ResourceLimits);limits.kwargs.update(http=0)
    client=TestClient(app,base_url='https://app.example.com',client=('127.0.0.1',123))
    assert client.get('/.env',headers={'CF-Connecting-IP':'203.0.113.4'}).status_code==503
    row=service.site_pause.traffic.rows[site['id']]
    assert row['requests']==1 and row['limited']==1 and row['scans']==1
    assert not waf.calls


@pytest.mark.parametrize('transition',['pause_api','site_save'])
def test_failed_pause_transition_rolls_back_attempt_marker_together(service,monkeypatch,transition):
    import sqlite3
    site,waf=prepared(service,monkeypatch)
    service.store.set('paused_auto_attempts',{site['id']:True})
    service.store.db.execute("CREATE TEMP TRIGGER refuse_attempt_write BEFORE INSERT ON kv WHEN NEW.key='paused_auto_attempts' BEGIN SELECT RAISE(ABORT,'test-only failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        if transition=='pause_api':service.set_site_paused(site['id'],False)
        else:service.save_site(site|{'paused':False})
    assert service.sites()[0]['paused']
    assert service.store.get('paused_auto_attempts')[site['id']] is True
    service.store.db.execute('DROP TRIGGER refuse_attempt_write')
    if transition=='pause_api':service.set_site_paused(site['id'],False)
    else:service.save_site(site|{'paused':False})
    assert not service.sites()[0]['paused']
    assert site['id'] not in service.store.get('paused_auto_attempts')


@pytest.mark.parametrize('peer,headers', [
    ('198.51.100.8', {'CF-Connecting-IP': '203.0.113.4'}), ('127.0.0.1', {}),
])
def test_rejected_nonconnector_traffic_skips_site_configuration_reads(service, monkeypatch, peer, headers):
    from lanbridge.gateway import ResourceLimits
    app = create_gateway(service)
    limits = next(m for m in app.user_middleware if m.cls is ResourceLimits)
    limits.kwargs.update(rate=0, burst=0)
    def unexpected_lookup(*args, **kwargs):
        raise AssertionError('already rejected nonconnector traffic must not read site configuration')
    monkeypatch.setattr(service, 'sites', unexpected_lookup)
    client = TestClient(app, base_url='https://app.example.com', client=(peer, 123))
    assert client.get('/', headers=headers).status_code == 429
    assert not service.site_pause.traffic.rows
