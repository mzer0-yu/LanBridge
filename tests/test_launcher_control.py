import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.launcher_control import control_instance
from lanbridge.service import Service
from test_security import service


def test_control_capability_is_local_instance_bound_and_not_browser_auth(service):
    calls=[]
    service.runtime_id='a'*32
    service.launcher_control_token=secrets.token_urlsafe(32)
    client=TestClient(create_admin(service,lambda:calls.append('stop'),restart=lambda:{'port':8892,'restarting':True}),base_url='http://127.0.0.1:8890')
    headers={'X-LanBridge-Control':service.launcher_control_token,'X-LanBridge-Instance':service.runtime_id}
    assert 'launcher_control' not in client.get('/api/bootstrap').json()
    assert client.post('/api/launcher/control',json={'operation':'stop'}).status_code==403
    for invalid in ({'X-LanBridge-Control':'wrong'}, {'X-LanBridge-Instance':'b'*32}, {'Origin':'http://127.0.0.1:8890'}):
        assert client.post('/api/launcher/control',json={'operation':'stop'},headers=headers|invalid).status_code==403
    assert client.get('/api/launcher/control',headers=headers).status_code==403
    assert client.post('/api/launcher/control',json={'operation':'bad'},headers=headers).status_code==400
    assert not calls
    assert client.post('/api/launcher/control',json={'operation':'restart'},headers=headers).json()['port']==8892
    assert calls==['stop']
    assert client.post('/api/launcher/control',json={'operation':'stop'},headers=headers).status_code==200
    remote=TestClient(create_admin(service,lambda:calls.append('remote'),remote=True),base_url='https://invalid.example.com')
    assert remote.post('/api/launcher/control',headers=headers,json={'operation':'stop'}).status_code==403
    assert calls==['stop','stop']


def test_two_instances_control_only_selected_and_restart_rotates_identity(tmp_path):
    project=Path(__file__).resolve().parents[1]
    mock=tmp_path/'mock';mock.mkdir()
    (mock/'webbrowser.py').write_text('def open(*args, **kwargs): return True\n',encoding='utf-8')
    env=os.environ.copy();env['PYTHONPATH']=str(mock)
    records=[]
    def wait_info(data):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                info=json.loads((data/'runtime.json').read_text(encoding='utf-8'))
                with httpx.Client(trust_env=False,timeout=1) as client:
                    response=client.get('http://127.0.0.1:'+str(info['admin_port'])+'/api/bootstrap')
                    if response.status_code==200:return info
            except (OSError,ValueError,httpx.HTTPError):pass
            time.sleep(.1)
        raise AssertionError('isolated runtime not ready')
    sockets=[]
    for _ in range(4):
        probe=socket.socket();probe.bind(('127.0.0.1',0));sockets.append(probe)
    ports=[probe.getsockname()[1] for probe in sockets]
    for probe in sockets:probe.close()
    try:
        for index in range(2):
            data=tmp_path/str(index);service=Service(data)
            service.store.set('settings',service.settings()|{'admin_port':ports[index*2],'gateway_port':ports[index*2+1]})
            service.store.db.close()
            process=subprocess.Popen([sys.executable,str(project/'run.py'),'--data-dir',str(data),'serve'],cwd=project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            records.append((data,process))
        first,second=[wait_info(data) for data,_ in records]
        with pytest.raises(ValueError):control_instance(records[0][0],second['instance'],'stop')
        result=control_instance(records[0][0],first['instance'],'restart')
        assert result['completed'] and result['port']==first['admin_port']
        assert records[0][1].wait(timeout=10)==0
        renewed=wait_info(records[0][0]);assert renewed['instance']!=first['instance']
        assert wait_info(records[1][0])['instance']==second['instance']
        with httpx.Client(trust_env=False) as client:
            assert client.post('http://127.0.0.1:'+str(renewed['admin_port'])+'/api/launcher/control',headers={'X-LanBridge-Control':first['launcher_control'],'X-LanBridge-Instance':first['instance']},json={'operation':'stop'}).status_code==403
        # Exercise the exact command used by the launcher; the capability stays out of arguments/output.
        result_file=tmp_path/'result.json'
        controlled=subprocess.run([sys.executable,str(project/'run.py'),'--data-dir',str(records[0][0]),'control','stop','--instance',renewed['instance'],'--result',str(result_file)],cwd=project,capture_output=True,text=True,encoding='utf-8',timeout=30)
        assert controlled.returncode==0
        assert renewed['launcher_control'] not in controlled.stdout
        assert json.loads(result_file.read_text(encoding='utf-8'))['completed']
        assert wait_info(records[1][0])['instance']==second['instance']
    finally:
        for data,process in records:
            try:
                info=json.loads((data/'runtime.json').read_text(encoding='utf-8'))
                control_instance(data,info['instance'],'stop',timeout=15)
            except (OSError,ValueError):pass
            if process.poll() is None:process.terminate()
            process.wait(timeout=10)
