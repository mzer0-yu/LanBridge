import json,time
import pytest
from lanbridge.aliyun_oauth import AliyunOAuth
from test_security import service


def profile():
    return dict(name='lanbridge',mode='OAuth',oauth_site_type='CN',region_id='cn-hangzhou',access_key_id='test-ak',access_key_secret='test-secret',sts_token='test-sts',oauth_refresh_token='test-refresh',sts_expiration=time.time()+3600)


def fake_run(config,args,authorize=False):
    assert config.is_file()
    initial=json.loads(config.read_text())['profiles'][0]
    assert initial['name']=='lanbridge' and initial['oauth_site_type']=='CN'
    config.write_text(json.dumps({'profiles':[profile()]}))
    return json.dumps({'AccountId':'12345','Arn':'test-arn'})


def test_authorization_saves_encrypted_profile_without_echo(service,monkeypatch):
    manager=service.domain_onboarding
    monkeypatch.setattr(manager.aliyun.oauth,'run',fake_run)
    result=manager.authorize_aliyun()
    assert result['auth_mode']=='oauth' and result['configured']
    assert 'test-secret' not in json.dumps(result)
    assert service.store.secret('aliyun_oauth_profile')
    assert not list((service.store.root/'aliyun-oauth').iterdir())
    assert manager.aliyun.oauth.credentials()==('test-ak','test-secret','test-sts')


def test_profile_disallows_untrusted_credential_execution(tmp_path):
    config=tmp_path/'config.json';data=profile();data['process_command']='bad';data['endpoint']='https://evil.example'
    config.write_text(json.dumps({'profiles':[data]}))
    safe=AliyunOAuth.read_profile(config)
    assert 'process_command' not in safe and 'endpoint' not in safe
    data['mode']='External';config.write_text(json.dumps({'profiles':[data]}))
    with pytest.raises(ValueError):AliyunOAuth.read_profile(config)


def test_cli_environment_ignores_ambient_keys(monkeypatch):
    monkeypatch.setenv('ALIBABA_CLOUD_ACCESS_KEY_ID','ambient')
    monkeypatch.setenv('DEBUG','sdk')
    env=AliyunOAuth.environment()
    assert 'ALIBABA_CLOUD_ACCESS_KEY_ID' not in env and 'DEBUG' not in env


def test_cli_failure_never_exposes_output(service,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(AliyunOAuth,'command',lambda self:'fake-cli')
    import subprocess
    original=subprocess.run
    monkeypatch.setattr('lanbridge.aliyun_oauth.subprocess.run',lambda command,*a,**k:SimpleNamespace(returncode=1,stdout=b'private',stderr=b'private-token') if command[0]=='fake-cli' else original(command,*a,**k))
    with pytest.raises(ValueError) as error:service.domain_onboarding.authorize_aliyun()
    assert 'private' not in str(error.value)
    assert not service.store.secret('aliyun_oauth_profile')


def test_rotation_does_not_invalidate_prepared_migration(service,monkeypatch):
    manager=service.domain_onboarding;monkeypatch.setattr(manager.aliyun.oauth,'run',fake_run)
    manager.authorize_aliyun();before=manager._context()
    updated=profile();updated['access_key_secret']='rotated'
    service.store.set_secret('aliyun_oauth_profile',json.dumps(updated))
    assert manager._context()==before
    service.store.set('aliyun_oauth_identity',{'AccountId':'different'})
    assert manager._context()!=before


def test_refresh_uses_cli_and_persists_rotation(service,monkeypatch):
    manager=service.domain_onboarding;expired=profile();expired['sts_expiration']=time.time()-1
    service.store.set_secret('aliyun_oauth_profile',json.dumps(expired))
    monkeypatch.setattr(manager.aliyun.oauth,'run',fake_run)
    assert manager.aliyun.oauth.credentials()[2]=='test-sts'
    assert json.loads(service.store.secret('aliyun_oauth_profile'))['sts_expiration']>time.time()


def test_refresh_rejects_account_change(service,monkeypatch):
    expired=profile();expired['sts_expiration']=time.time()-1
    service.store.set_secret('aliyun_oauth_profile',json.dumps(expired))
    service.store.set('aliyun_oauth_identity',{'AccountId':'original'})
    monkeypatch.setattr(service.domain_onboarding.aliyun.oauth,'run',fake_run)
    with pytest.raises(ValueError,match='身份发生变化'):
        service.domain_onboarding.aliyun.oauth.credentials()
    assert json.loads(service.store.secret('aliyun_oauth_profile'))['sts_expiration']<time.time()


def test_cli_default_home_stays_inside_temporary_configuration(service,monkeypatch):
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    original=subprocess.run
    captured={}
    def run(command,*args,**kwargs):
        if command[0]!='fake-cli':return original(command,*args,**kwargs)
        captured.update(kwargs)
        config=Path(command[-1])
        assert Path(kwargs['env']['USERPROFILE'])/'.aliyun'/'config.json'==config
        assert kwargs['env']['HOMEDRIVE']=='' and kwargs['env']['HOMEPATH']==kwargs['env']['USERPROFILE']
        assert config.is_file()
        return SimpleNamespace(returncode=0,stdout=b'{}')
    monkeypatch.setattr(AliyunOAuth,'command',lambda self:'fake-cli')
    monkeypatch.setattr(subprocess,'run',run)
    oauth=service.domain_onboarding.aliyun.oauth
    with oauth.configuration() as config:
        assert oauth.run(config,['sts','GetCallerIdentity'])=='{}'
    assert not Path(captured['env']['USERPROFILE']).exists()


def test_oauth_endpoint_keeps_local_admin_and_csrf_boundary(service,monkeypatch):
    from test_security import admin_client
    monkeypatch.setattr(service.domain_onboarding.aliyun.oauth,'run',fake_run)
    client=admin_client(service)
    assert client.post('/api/domain-onboarding/oauth',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/domain-onboarding/oauth',json={},headers={'X-CSRF-Token':'wrong'}).status_code==403
    response=client.post('/api/domain-onboarding/oauth',json={})
    assert response.status_code==200 and response.json()['auth_mode']=='oauth'
    assert 'test-refresh' not in response.text and 'test-secret' not in response.text
