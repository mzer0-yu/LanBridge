import threading
from fastapi.testclient import TestClient
import pytest
from lanbridge.admin import create_admin
from lanbridge.site_publication import SitePublication
from test_security import service, admin_client


def body():
    return {'name':'Test website','hostname':'app.example.com','origin':'http://127.0.0.1:9300','human_check':True,'background':True}


def wait(service):
    service.site_publication.thread.join(5)
    assert not service.site_publication.thread.is_alive()
    return service.site_publication.status()


def test_save_returns_before_cloud_and_state_remains_readable(service,monkeypatch):
    entered,release=threading.Event(),threading.Event()
    def widget(**kwargs):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(service.cf,'create_widget',widget)
    monkeypatch.setattr(service.cf,'plan',lambda:{'revision':'test'})
    monkeypatch.setattr(service.cf,'apply',lambda rev:service.store.set('published_hosts',['app.example.com']))
    owner=admin_client(service)
    try:
        response=owner.post('/api/sites',json=body())
        assert response.status_code==200 and response.json()['saved']
        assert entered.wait(2)
        assert not release.is_set()
        state=owner.get('/api/state').json()
        assert state['sites'][0]['hostname']=='app.example.com'
        assert state['site_publication']['phase']=='verification'
        assert owner.post('/api/sites',json=body()).status_code==400
        assert len(service.sites())==1
    finally:
        release.set()
        wait(service)
    assert service.site_publication.status()['phase']=='succeeded'


def test_failure_keeps_local_save_and_retry_runs_without_repeating_save(service,monkeypatch):
    monkeypatch.setattr(service.cf,'create_widget',lambda **kw:(_ for _ in ()).throw(RuntimeError('Cloudflare temporarily unavailable')))
    data=body();data.pop('background');data.update(passcode_required=True,passcode='test-only-secret-123')
    result=service.site_publication.submit(data)
    job=wait(service)
    assert job['phase']=='failed' and job['saved']
    assert 'test-only-secret' not in str(job)
    site=service.sites()[0]
    secret=service.store.secret('passcode_'+site['id'])
    monkeypatch.setattr(service.cf,'create_widget',lambda **kw:None)
    monkeypatch.setattr(service.cf,'plan',lambda:{'revision':'test'})
    monkeypatch.setattr(service.cf,'apply',lambda rev:service.store.set_many({'published_hosts':['app.example.com'],'publication_error':None}))
    service.site_publication.submit()
    assert wait(service)['phase']=='succeeded'
    assert service.sites()==[site]
    assert service.store.secret('passcode_'+site['id'])==secret


def test_invalid_local_save_never_starts_cloud_or_creates_job(service,monkeypatch):
    monkeypatch.setattr(service.cf,'create_widget',lambda **kw:pytest.fail('Cloud must not be called'))
    with pytest.raises(ValueError):
        service.site_publication.submit(body() | {'hostname':'other.com'})
    assert service.sites()==[] and service.site_publication.status() is None


def test_recovery_is_explicit_and_does_not_replay_cloud_operations(service):
    service.store.set('site_publication_job',{'id':'old','phase':'publishing','saved':True})
    manager=SitePublication(service)
    assert manager.status()['phase']=='publishing'
    manager.recover()
    assert manager.status()['phase']=='failed' and manager.thread is None


def test_async_save_retains_api_permissions_and_csrf(service):
    owner=admin_client(service)
    assert owner.post('/api/sites',json=body(),headers={'X-CSRF-Token':'invalid'}).status_code==403
    grant=owner.post('/api/temporary-tokens',json={'permissions':['account']}).json()
    limited=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
    assert limited.post('/api/sites',json=body()).status_code==403
    assert service.site_publication.status() is None


def test_thread_start_failure_reports_saved_configuration_and_can_stop(service,monkeypatch):
    monkeypatch.setattr(threading.Thread,'start',lambda thread:(_ for _ in ()).throw(RuntimeError('thread unavailable')))
    data=body();data.pop('background')
    result=service.site_publication.submit(data)
    assert result['saved'] and result['publication']['status']=='failed'
    assert service.site_publication.status()['phase']=='failed'
    assert len(service.sites())==1
    service.site_publication.stop()


@pytest.mark.parametrize("stage", ["widget", "plan"])
def test_stopping_during_verification_does_not_start_publication(service, monkeypatch, stage):
    calls = []
    def widget(**kwargs):
        calls.append("widget")
        if stage == "widget":
            service.site_publication.stopping = True
    def plan():
        calls.append("plan")
        service.site_publication.stopping = True
        return {"revision": "test"}
    monkeypatch.setattr(service.cf, "create_widget", widget)
    monkeypatch.setattr(service.cf, "plan", plan)
    monkeypatch.setattr(service.cf, "apply", lambda revision: calls.append("apply"))
    data = body(); data.pop("background")
    service.site_publication.submit(data)
    job = wait(service)
    assert calls == (["widget"] if stage == "widget" else ["widget", "plan"])
    assert job["phase"] == "failed" and job["saved"]
    assert service.sites()[0]["hostname"] == 'app.example.com'
