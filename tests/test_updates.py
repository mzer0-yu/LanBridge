from pathlib import Path
import threading
from types import SimpleNamespace
import pytest
from lanbridge.updates import UpdateController, revision


def setup(tmp_path, enabled=True):
    (tmp_path/'lanbridge').mkdir();(tmp_path/'ui').mkdir()
    (tmp_path/'run.py').write_text('value=1')
    calls=[];now=[0]
    service=SimpleNamespace(lock=threading.RLock())
    control=UpdateController(tmp_path,service,lambda:calls.append(True),enabled=enabled,settle_seconds=1,notice_seconds=1,clock=lambda:now[0])
    return control,calls,now,service


def test_valid_change_reloads_once_after_settle(tmp_path):
    control,calls,now,_=setup(tmp_path)
    (tmp_path/'run.py').write_text('value=2');control.tick()
    assert control.status()['phase']=='pending' and not calls
    now[0]=3;control.tick();control.tick()
    assert calls==[True] and control.status()['phase']=='reloading'


def test_partial_invalid_update_keeps_old_instance_and_recovers(tmp_path):
    control,calls,now,_=setup(tmp_path)
    (tmp_path/'run.py').write_text('value=');control.tick();now[0]=3;control.tick()
    assert control.status()['phase']=='error' and not calls
    (tmp_path/'run.py').write_text('value=3');control.tick();now[0]=6;control.tick()
    assert calls==[True]


def test_disabled_reloader_explains_restart(tmp_path):
    control,calls,now,_=setup(tmp_path,False)
    (tmp_path/'run.py').write_text('value=2');control.tick();now[0]=30;control.tick()
    assert control.status()['message']=='更新待生效，请重启实例' and not calls


def test_runtime_data_tests_and_touch_do_not_reload(tmp_path):
    control,calls,now,_=setup(tmp_path)
    (tmp_path/'data').mkdir();(tmp_path/'data'/'state.json').write_text('{}')
    (tmp_path/'run.py').touch();control.tick()
    assert control.status()['phase']=='current' and not calls


def test_authorization_defers_reload(tmp_path):
    control,calls,now,service=setup(tmp_path)
    phase=['authorizing'];service.browser_auth=SimpleNamespace(status=lambda:{'phase':phase[0]})
    (tmp_path/'run.py').write_text('value=2');control.tick();now[0]=3;control.tick()
    assert not calls and '授权' in control.status()['message']
    phase[0]='done';control.tick();assert calls==[True]


def test_busy_migration_defers_reload(tmp_path):
    control,calls,now,service=setup(tmp_path)
    lock=threading.Lock();service.domain_onboarding=SimpleNamespace(lock=lock)
    (tmp_path/'run.py').write_text('value=2');control.tick();now[0]=3
    with lock:control.tick()
    assert not calls
    control.tick();assert calls==[True]


def test_frontend_only_change_does_not_restart(tmp_path):
    control,calls,now,_=setup(tmp_path)
    (tmp_path/'ui'/'app.js').write_text('1');control.tick()
    assert control.status()['phase']=='frontend' and not calls


def test_removed_backend_file_is_a_change(tmp_path):
    control,calls,now,_=setup(tmp_path)
    (tmp_path/'run.py').unlink();control.tick();now[0]=3;control.tick()
    assert not calls and control.status()['phase']=='error'


def test_failed_startup_validation_never_stops_live_instance(tmp_path):
    control,calls,now,_=setup(tmp_path)
    probes=[];control.preflight=lambda:probes.append(True) or False
    (tmp_path/'run.py').write_text('value=2');control.tick();now[0]=3;control.tick();control.tick()
    assert not calls and probes==[True] and control.status()['phase']=='error'
    control.preflight=lambda:True
    (tmp_path/'run.py').write_text('value=3');control.tick();now[0]=6;control.tick()
    assert calls==[True]


def test_real_process_reload_keeps_port_and_restores_instance(tmp_path):
    import json, shutil, socket, subprocess, sys, time
    import httpx
    source=Path(__file__).resolve().parents[1]
    project=tmp_path/'project';project.mkdir()
    shutil.copy(source/'run.py',project/'run.py')
    shutil.copytree(source/'lanbridge',project/'lanbridge',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(source/'ui',project/'ui')
    def free_port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
    admin,gateway=free_port(),free_port()
    while gateway==admin:gateway=free_port()
    data=project/'data'
    process=subprocess.Popen([sys.executable,str(project/'run.py'),'--data-dir',str(data),'serve','--admin-port',str(admin),'--gateway-port',str(gateway),'--no-resume-connector'],cwd=project,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def wait_instance(previous=None):
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            try:
                value=httpx.get(f'http://127.0.0.1:{admin}/api/bootstrap',timeout=1,trust_env=False).json()
                if value.get('instance') and value['instance']!=previous:return value['instance']
            except (httpx.HTTPError,ValueError):pass
            time.sleep(.2)
        raise AssertionError('isolated instance failed to become ready')
    try:
        first=wait_instance()
        with (project/'run.py').open('a',encoding='utf-8') as stream:stream.write('\n# integration reload generation\n')
        second=wait_instance(first)
        assert second!=first
        marker=json.loads((data/'runtime.json').read_text())
        assert marker['admin_port']==admin
        assert process.wait(timeout=5)==0
    finally:
        try:
            marker=json.loads((data/'runtime.json').read_text())
            httpx.post(f'http://127.0.0.1:{admin}/api/launcher/control',json={'operation':'stop'},headers={'X-LanBridge-Control':marker['launcher_control'],'X-LanBridge-Instance':marker['instance']},timeout=3,trust_env=False)
            deadline=time.monotonic()+12
            while (data/'runtime.json').exists() and time.monotonic()<deadline:time.sleep(.1)
            assert not (data/'runtime.json').exists()
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=5)
