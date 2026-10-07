from pathlib import Path

import httpx
import pytest

from lanbridge import tools
from test_security import service


def test_update_check_compares_versions_without_install(tmp_path, monkeypatch):
    binary = tmp_path / 'old.exe'
    binary.write_bytes(b'old')
    monkeypatch.setattr(tools, 'version', lambda _: 'cloudflared version 2026.1.0 (build)')
    original = httpx.Client
    monkeypatch.setattr(tools.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'tag_name':'2026.2.0'})), **kwargs))
    result = tools.check_cloudflared_update(str(binary))
    assert result['available'] and result['latest'] == '2026.2.0'
    assert binary.read_bytes() == b'old'


@pytest.mark.parametrize('running,fail', [(False,False),(True,False),(True,True)])
def test_update_preserves_old_binary_and_restores_runtime(service, tmp_path, monkeypatch, running, fail):
    old, new = tmp_path/'old.exe', tmp_path/'new.exe'
    old.write_bytes(b'old');new.write_bytes(b'new')
    cfg=service.settings();cfg['cloudflared_path']=str(old);service.store.set('settings',cfg)
    runtime={'running':running}
    monkeypatch.setattr(service.connector,'check_update',lambda:{'available':True,'latest':'2026.2.0'})
    monkeypatch.setattr(service.connector,'status',lambda:{'running':runtime['running']})
    monkeypatch.setattr(service.connector,'stop',lambda:runtime.update(running=False))
    def start():
        if fail and service.settings()['cloudflared_path']==str(new):
            raise ValueError('bad new binary')
        runtime['running']=True
    monkeypatch.setattr(service.connector,'start',start)
    monkeypatch.setattr(tools,'ensure_cloudflared',lambda **kwargs:{'path':str(new),'version':'cloudflared version 2026.2.0','source':'download'})
    if fail:
        with pytest.raises(ValueError,match='已恢复'):
            service.connector.update('2026.2.0')
        assert service.settings()['cloudflared_path']==str(old)
    else:
        assert service.connector.update('2026.2.0')['running_restored']==running
        assert service.settings()['cloudflared_path']==str(new)
    assert runtime['running']==running
    assert old.read_bytes()==b'old'


def test_invalid_update_tag_rejected_before_download():
    with pytest.raises(ValueError,match='版本无效'):
        tools.ensure_cloudflared(update_tag='../overwrite')


def test_status_caches_version_and_invalidates_on_binary_change(service, tmp_path, monkeypatch):
    binary = tmp_path / 'cloudflared.exe'
    binary.write_bytes(b'old')
    cfg = service.settings()
    cfg['cloudflared_path'] = str(binary)
    service.store.set('settings', cfg)
    calls = []
    def read_version(path):
        calls.append(path)
        return 'cloudflared version ' + ('2026.1.0' if binary.read_bytes() == b'old' else '2026.2.0')
    monkeypatch.setattr(tools, 'version', read_version)
    assert service.connector.status()['version'] == 'cloudflared version 2026.1.0'
    assert service.connector.status()['installed']
    assert len(calls) == 1
    binary.write_bytes(b'new binary')
    assert service.connector.status()['version'] == 'cloudflared version 2026.2.0'
    assert len(calls) == 2
    binary.unlink()
    status = service.connector.status()
    assert not status['installed'] and status['version'] == ''
