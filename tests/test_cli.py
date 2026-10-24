import argparse
import json
from pathlib import Path
import subprocess
import sys
import httpx
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.cli import OnlineClient, api_path, base_url, endpoint_catalogue, main, redact, request_data
from test_security import service, admin_client


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com/?token=x', 'file:///tmp/x', 'https://example.com/#x'])
def test_unsafe_management_url_rejected(tmp_path, url):
    with pytest.raises(ValueError): base_url(url, tmp_path)


@pytest.mark.parametrize('path', ['https://evil.test/api/state', '//evil.test/api/state', '/other', '/api/../state', '/api/%2e%2e/state', '/api/x\\y', '/api/x#fragment', '/api/sites/{id}/pause'])
def test_unsafe_request_path_rejected(path):
    with pytest.raises(ValueError): api_path(path)


def test_runtime_url_discovery_is_read_only(tmp_path):
    assert base_url(None, tmp_path) == 'http://127.0.0.1:8890'
    assert list(tmp_path.iterdir()) == []
    (tmp_path / 'runtime.json').write_text('{"admin_port":9000}')
    assert base_url(None, tmp_path) == 'http://127.0.0.1:9000'
    (tmp_path / 'runtime.json').write_text('{"admin_port":true}')
    with pytest.raises(ValueError): base_url(None, tmp_path)


def test_catalogue_covers_all_api_routes_and_dynamic_actions(service):
    app = create_admin(service)
    catalogue = endpoint_catalogue()
    for route in app.routes:
        if not route.path.startswith('/api/'): continue
        template = route.path
        candidates = [item for item in catalogue if item['method'] in route.methods]
        if '{action}' in template:
            prefix, suffix = template.split('{action}')
            assert any(item['path'].startswith(prefix) and item['path'].endswith(suffix) and '{action}' not in item['path'] for item in candidates)
        else:
            assert any(item['path'] == template for item in candidates)
    paths = {item['path'] for item in catalogue}
    assert {'/api/cloudflare/browser-authorize-select-zone', '/api/cloudflare/apply', '/api/connector/update', '/api/domain-onboarding/confirm', '/api/domain-onboarding/finish'} <= paths


def test_login_csrf_persistent_encrypted_session_and_logout(service, tmp_path):
    owner = admin_client(service)
    folder = tmp_path / 'profiles'
    online = OnlineClient('http://127.0.0.1:8890', folder, transport=owner._transport)
    try:
        with pytest.raises(ValueError, match='401'): online.request('GET', '/api/state')
        result = online.request('POST', '/api/login', {'username': 'admin', 'password': 'correct horse battery'})
        assert result['csrf'] == online.csrf
        raw = online.profile.read_bytes()
        assert online.csrf.encode() not in raw and b'correct horse battery' not in raw
        online.close()
        online = OnlineClient('http://127.0.0.1:8890', folder, transport=owner._transport)
        assert online.request('GET', '/api/state')['sites'] == []
        assert online.request('POST', '/api/public-client', {'enabled': False})['enabled'] is False
        online.request('POST', '/api/logout', {})
        assert not online.profile.exists()
        with pytest.raises(ValueError, match='401'): online.request('GET', '/api/state')
    finally:
        online.close()


def test_limited_bearer_retains_permissions_without_profile(service, tmp_path):
    owner = admin_client(service)
    grant = owner.post('/api/temporary-tokens', json={'name': 'CLI test', 'permissions': ['sites']}).json()
    online = OnlineClient('http://127.0.0.1:8890', tmp_path / 'profiles', token=grant['token'], transport=owner._transport)
    try:
        assert online.request('GET', '/api/state')['access_scope'] == 'sites'
        with pytest.raises(ValueError, match='403'): online.request('POST', '/api/public-client', {'enabled': False})
        assert not online.profile.exists()
    finally: online.close()


def test_redirect_never_forwards_secret_or_retries(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={'location': 'https://evil.example/api/login'})
    online = OnlineClient('https://good.example', tmp_path, token='not-a-real-secret', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match='重定向'): online.request('POST', '/api/credentials', {'cf_write_token': 'private'})
        assert len(calls) == 1 and calls[0].url.host == 'good.example'
    finally: online.close()


def test_error_body_not_echoed_and_failed_download_preserves_existing_file(tmp_path):
    online = OnlineClient('https://good.example', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(lambda request: httpx.Response(403, json={'detail': 'SECRET-ECHO'})))
    target = tmp_path / 'log.jsonl'
    try:
        with pytest.raises(ValueError) as error: online.request('POST', '/api/settings', {}, output=target)
        assert 'SECRET-ECHO' not in str(error.value) and not target.exists()
    finally: online.close()


def test_export_stream_and_existing_file_never_overwritten(tmp_path):
    online = OnlineClient('https://good.example', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"event":"test"}\n', headers={'content-type': 'application/x-ndjson'})))
    target = tmp_path / 'log.jsonl'
    try:
        online.request('GET', '/api/audit/export', output=target)
        assert target.read_bytes() == b'{"event":"test"}\n'
        with pytest.raises(ValueError, match='未发送请求'): online.request('GET', '/api/audit/export', output=target)
        assert target.read_bytes() == b'{"event":"test"}\n'
    finally: online.close()


def test_body_input_and_secret_redaction(tmp_path, monkeypatch):
    source = tmp_path / 'input.json'
    source.write_text('{"name":"App"}', encoding='utf-8')
    monkeypatch.setattr('getpass.getpass', lambda prompt: 'private-input')
    data = request_data(argparse.Namespace(file=str(source), field=['enabled=false', 'allowed_countries=["CN"]'], secret=['passcode']))
    assert data == {'name': 'App', 'enabled': False, 'allowed_countries': ['CN'], 'passcode': 'private-input'}
    result = redact({'nested': {'token': 'hidden', 'cf_write_token': True}, 'password': 'hidden'})
    assert 'hidden' not in json.dumps(result) and result['nested']['cf_write_token'] is True
    source.write_text('[')
    with pytest.raises(ValueError): request_data(argparse.Namespace(file=str(source), field=[], secret=[]))


def test_commands_need_no_service_or_data_dir(tmp_path, capsys):
    data = tmp_path / 'must-not-exist'
    assert main(['--data-dir', str(data), 'commands']) == 0
    assert json.loads(capsys.readouterr().out)['endpoints']
    assert not data.exists()


@pytest.mark.parametrize('entry', [['run.py'], ['-m', 'lanbridge']])
@pytest.mark.parametrize('options', ['root', 'root_with_url', 'root_equals'])
def test_source_entry_points_from_different_working_directory(tmp_path, entry, options):
    root = Path(__file__).resolve().parents[1]
    import os
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING='utf-8')
    if entry == ['run.py']: entry = [str(root / 'run.py')]
    arguments = ['--data-dir', str(tmp_path / 'absent'), 'online', 'commands']
    if options == 'root_with_url': arguments = ['--data-dir', str(tmp_path / 'absent'), 'online', '--url', 'http://127.0.0.1:9000', 'commands']
    if options == 'root_equals': arguments = ['--data-dir=' + str(tmp_path / 'absent'), 'online', '--url', 'http://127.0.0.1:9000', 'commands']
    result = subprocess.run([sys.executable, '-B', *entry, *arguments], cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['schema'] == 'lanbridge-api-commands/v1'
    assert not (tmp_path / 'absent').exists()


@pytest.mark.parametrize('command,extra,method,path', [
    ('state', [], 'GET', '/api/state'), ('sites', [], 'GET', '/api/state'),
    ('protection', [], 'GET', '/api/state'), ('save', ['--field', 'name=Test'], 'POST', '/api/sites'),
    ('pause', ['site-id', '--field', 'cloudflare=true'], 'POST', '/api/sites/site-id/pause'),
    ('resume', ['site-id'], 'POST', '/api/sites/site-id/pause'),
    ('probe', ['site-id'], 'POST', '/api/sites/site-id/probe'),
    ('retry-publish', [], 'POST', '/api/sites'),
    ('connector', ['start'], 'POST', '/api/connector/start'),
    ('cloudflare', ['browser-authorize'], 'POST', '/api/cloudflare/browser-authorize'),
    ('domain', ['prepare', '--field', 'domain=example.com'], 'POST', '/api/domain-onboarding/prepare'),
    ('domains', [], 'GET', '/api/domain-onboarding'),
    ('logs', [], 'GET', '/api/audit'), ('tokens', [], 'GET', '/api/temporary-tokens'),
    ('issue-token', ['--show-secrets'], 'POST', '/api/temporary-tokens'),
    ('revoke-token', ['token-id'], 'POST', '/api/temporary-tokens/token-id/revoke'),
    ('credentials', ['--secret', 'cf_write_token'], 'POST', '/api/credentials'),
    ('settings', ['--field', 'zone_name=example.com'], 'POST', '/api/settings'),
    ('restart', [], 'POST', '/api/restart'), ('shutdown', [], 'POST', '/api/shutdown'),
    ('api', ['POST', '/api/zones/zone-id/default'], 'POST', '/api/zones/zone-id/default'),
])
def test_shortcuts_dispatch_to_same_api(tmp_path, monkeypatch, capsys, command, extra, method, path):
    import lanbridge.cli as cli
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'sites': [], 'visitor_protection': {}, 'token': 'returned-test-token'})
    original = cli.OnlineClient
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('LANBRIDGE_TOKEN', 'test-cli-bearer')
    monkeypatch.setattr('getpass.getpass', lambda prompt: 'secret-test-input')
    assert main(['--profile-dir', str(tmp_path), command, *extra]) == 0
    assert len(calls) == 1 and calls[0].method == method and calls[0].url.path == path
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] is True
    if command == 'pause': assert json.loads(calls[0].content) == {'paused': True, 'cloudflare': True}
    if command == 'resume': assert json.loads(calls[0].content)['paused'] is False
    if command == 'save': assert json.loads(calls[0].content)['background'] is True
    if command == 'retry-publish': assert json.loads(calls[0].content) == {'background': True, 'retry_publication': True}
    if command == 'issue-token': assert result['result']['token'] == 'returned-test-token'
    elif command not in {'sites', 'protection'}: assert result['result']['token'] != 'returned-test-token'


def test_secret_field_cannot_be_put_in_command_arguments(tmp_path):
    with pytest.raises(ValueError, match='秘密字段'):
        request_data(argparse.Namespace(file=None, field=['cf_write_token=test'], secret=[]))


def test_output_conflict_fails_before_any_network_write(tmp_path):
    calls = []
    online = OnlineClient('https://example.com', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(lambda req: calls.append(req)))
    target = tmp_path / 'existing'; target.write_text('keep')
    try:
        with pytest.raises(ValueError, match='未发送请求'): online.request('POST', '/api/settings', {}, output=target)
        assert calls == [] and target.read_text() == 'keep'
    finally: online.close()


def test_broken_profile_can_be_cleared_without_loading_it(tmp_path, capsys):
    import hashlib
    target = tmp_path / (hashlib.sha256(b'http://127.0.0.1:8890').hexdigest() + '.bin')
    target.write_bytes(b'broken')
    assert main(['--profile-dir', str(tmp_path), 'logout', '--local']) == 0
    assert not target.exists() and json.loads(capsys.readouterr().out)['ok']


@pytest.mark.parametrize('operation', [item for item in endpoint_catalogue() if item['path'] != '/api/launcher/control'], ids=lambda item: item['command'])
def test_every_management_operation_has_named_cli_dispatch(tmp_path, monkeypatch, capsys, operation):
    import lanbridge.cli as cli
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'sites': [], 'visitor_protection': {}, 'csrf': 'test-csrf'})
    original = cli.OnlineClient
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.delenv('LANBRIDGE_TOKEN', raising=False)
    monkeypatch.delenv('LANBRIDGE_PASSWORD', raising=False)
    monkeypatch.setattr('getpass.getpass', lambda prompt: 'test-password-only')
    arguments = ['--profile-dir', str(tmp_path), operation['command']]
    path = operation['path']
    for parameter in operation['parameters']:
        arguments.extend(['--' + parameter.replace('_', '-'), 'test-id'])
        path = path.replace('{' + parameter + '}', 'test-id')
    assert main(arguments) == 0
    assert len(calls) == 1 and calls[0].method == operation['method'] and calls[0].url.path == path
    assert json.loads(capsys.readouterr().out)['ok']


def test_internal_launcher_operation_uses_existing_control_command(tmp_path, monkeypatch, capsys):
    from run import main as root_main
    calls = []
    monkeypatch.setattr('lanbridge.launcher_control.control_instance', lambda root, instance, operation: calls.append((instance, operation)) or {'completed': True})
    assert root_main(['--data-dir', str(tmp_path), 'control', 'stop', '--instance', 'a' * 32]) == 0
    assert calls == [('a' * 32, 'stop')]
    assert json.loads(capsys.readouterr().out)['ok']


@pytest.mark.parametrize('command,arguments,expected', [
    ('save-site', ['--name', '123', '--hostname', 'app.example.com', '--origin', 'http://127.0.0.1:9300', '--enabled', 'false', '--allowed-countries', 'CN,HK', '--protocols', 'http,websocket'], {'name': '123', 'enabled': False, 'allowed_countries': ['CN', 'HK'], 'protocols': ['http', 'websocket'], 'background': True}),
    ('site-pause-mode', ['--site-id', 'site-id', '--paused', 'true', '--cloudflare', 'false'], {'paused': True, 'cloudflare': False}),
    ('change-password', ['--current', '--password'], {'current': 'test-secret', 'password': 'test-secret'}),
    ('domain-onboarding-credentials', ['--access-key-id', '--access-key-secret'], {'access_key_id': 'test-secret', 'access_key_secret': 'test-secret'}),
    ('admin-port', ['--port', '9000'], {'port': 9000}),
    ('issue-token', ['--name', 'Agent', '--hours', '2', '--permissions', 'sites,account'], {'name': 'Agent', 'hours': 2, 'permissions': ['sites', 'account']}),
])
def test_native_option_types_and_hidden_secrets(tmp_path, monkeypatch, capsys, command, arguments, expected):
    import lanbridge.cli as cli
    calls = []
    original = cli.OnlineClient
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('LANBRIDGE_TOKEN', 'test-token')
    monkeypatch.setattr('getpass.getpass', lambda prompt: 'test-secret')
    assert main(['--profile-dir', str(tmp_path), command, *arguments]) == 0
    payload = json.loads(calls[0].content)
    assert all(payload[key] == value for key, value in expected.items())
    assert 'test-secret' not in capsys.readouterr().out


def test_catalogue_names_unique_and_all_routes_accounted_for():
    operations = endpoint_catalogue()
    assert len({item['command'] for item in operations}) == len(operations)
    assert all(item['entry'].startswith('lanbridge ') for item in operations)
    assert next(item for item in operations if item['command'] == 'cloudflare-apply')['fields'] == ['revision']
    assert next(item for item in operations if item['command'] == 'connector-start')['fields'] == []
    assert next(item for item in operations if item['command'] == 'cloudflare-browser-authorize')['fields'] == ['browser', 'require_waf']


@pytest.mark.parametrize('command,arguments,expected', [
    ('logs', ['--before', '123'], 'before=123'),
    ('domain-onboarding-connected-domains', ['--refresh', 'true'], 'refresh=1'),
    ('domain-onboarding-connected-domains', ['--refresh', 'false'], 'refresh=0'),
])
def test_native_query_flags_need_no_api_path(tmp_path, monkeypatch, capsys, command, arguments, expected):
    import lanbridge.cli as cli
    original = cli.OnlineClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('LANBRIDGE_TOKEN', 'test-token')
    assert main(['--profile-dir', str(tmp_path), command, *arguments]) == 0
    assert calls[0].url.query.decode() == expected


def test_named_command_preserves_actual_api_permission_rejection(service, tmp_path, monkeypatch, capsys):
    import lanbridge.cli as cli
    owner = admin_client(service)
    grant = owner.post('/api/temporary-tokens', json={'name': 'limited', 'permissions': ['sites']}).json()
    original = cli.OnlineClient
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=owner._transport))
    monkeypatch.setenv('LANBRIDGE_TOKEN', grant['token'])
    assert main(['--profile-dir', str(tmp_path), 'public-client', '--enabled', 'false']) == 1
    assert '403' in json.loads(capsys.readouterr().out)['error']
    assert service.store.get('public_client_enabled', True) is True


@pytest.mark.parametrize('command,arguments,expected', [
    ('issue-token', ['--hours', '0.5'], {'hours': 0.5}),
    ('log-settings', ['--limit-mb', '1.5'], {'limit_mb': 1.5}),
    ('credentials', ['--remove-cf-read-token', 'true'], {'remove_cf_read_token': True}),
    ('cloudflare-token-connect', ['--save-token', 'false'], {'save_token': False}),
])
def test_review_native_parameters_match_web_types(tmp_path, monkeypatch, capsys, command, arguments, expected):
    import lanbridge.cli as cli
    original = cli.OnlineClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('LANBRIDGE_TOKEN', 'test-only-token')
    assert main(['--profile-dir', str(tmp_path), command, *arguments]) == 0
    assert json.loads(calls[0].content) == expected


@pytest.mark.parametrize('key', ['remove_cf_read_token', 'save_token'])
def test_review_boolean_token_fields_are_not_secrets(key):
    assert request_data(argparse.Namespace(file=None, field=[key + '=false'], secret=[])) == {key: False}


def test_review_login_download_preserves_csrf_for_following_write(service, tmp_path):
    owner = admin_client(service)
    online = OnlineClient('http://127.0.0.1:8890', tmp_path / 'profiles', transport=owner._transport)
    try:
        online.request('POST', '/api/login', {'username': 'admin', 'password': 'correct horse battery'}, output=tmp_path / 'login.json')
        online.close()
        online = OnlineClient('http://127.0.0.1:8890', tmp_path / 'profiles', transport=owner._transport)
        assert online.request('POST', '/api/public-client', {'enabled': False})['enabled'] is False
    finally: online.close()


def test_review_unwritable_output_fails_before_remote_write(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'saved': True})
    online = OnlineClient('https://example.com', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises((ValueError, OSError)):
            online.request('POST', '/api/settings', {}, output=tmp_path / 'absent' / 'result.json')
        assert calls == []
    finally: online.close()


@pytest.mark.parametrize('arguments', [
    ['--url', 'https://\u200d.example', 'state'],
    ['--timeout', 'nan', 'state'], ['--timeout', '-1', 'state'],
])
def test_review_invalid_connection_options_fail_without_traceback_or_request(monkeypatch, capsys, arguments):
    monkeypatch.setattr('lanbridge.cli.OnlineClient', lambda *a, **kw: pytest.fail('Invalid options must not connect'))
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False


@pytest.mark.parametrize('command,arguments', [
    ('issue-token', ['--hours', 'nan']), ('log-settings', ['--limit-mb', 'inf']),
])
def test_review_nonfinite_native_values_never_send_request(tmp_path, monkeypatch, capsys, command, arguments):
    import lanbridge.cli as cli
    original = cli.OnlineClient
    calls = []
    monkeypatch.setenv('LANBRIDGE_TOKEN', 'test-token')
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(lambda request: calls.append(request))))
    assert main(['--profile-dir', str(tmp_path), command, *arguments]) == 1
    assert not calls and json.loads(capsys.readouterr().out)['ok'] is False


def test_review_decimal_controls_work_with_real_management_api(service, tmp_path, monkeypatch, capsys):
    import lanbridge.cli as cli
    owner = admin_client(service)
    original = cli.OnlineClient
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=owner._transport))
    monkeypatch.delenv('LANBRIDGE_TOKEN', raising=False)
    monkeypatch.setenv('LANBRIDGE_PASSWORD', 'correct horse battery')
    prefix = ['--profile-dir', str(tmp_path / 'profiles')]
    assert main([*prefix, 'login']) == 0
    capsys.readouterr()
    assert main([*prefix, 'log-settings', '--limit-mb', '1.5']) == 0
    assert service.store.audit_limit_mb() == 1.5
    capsys.readouterr()
    assert main([*prefix, 'issue-token', '--hours', '0.5']) == 0
    token = json.loads(capsys.readouterr().out)['result']
    row = service.store.db.execute('SELECT created,expires FROM temporary_tokens WHERE id=?', (token['id'],)).fetchone()
    assert row['expires'] - row['created'] == 1800


def test_review_partial_download_is_removed_without_retry(tmp_path):
    calls = []
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'partial'
            raise httpx.ReadError('test interrupted response')
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenStream())
    target = tmp_path / 'partial.json'
    online = OnlineClient('https://example.com', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.ReadError): online.request('POST', '/api/settings', {}, output=target)
        assert len(calls) == 1 and not target.exists()
    finally: online.close()


def test_review_late_disk_failure_reports_accepted_operation(tmp_path, monkeypatch):
    target = tmp_path / 'failed.json'
    original = Path.open
    class FailedOutput:
        def __init__(self, stream): self.stream = stream
        def write(self, chunk): raise OSError('test disk full')
        def flush(self): self.stream.flush()
        def close(self): self.stream.close()
    def open_output(path, *a, **kw):
        stream = original(path, *a, **kw)
        return FailedOutput(stream) if path == target else stream
    monkeypatch.setattr(Path, 'open', open_output)
    online = OnlineClient('https://example.com', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'saved': True})))
    try:
        with pytest.raises(ValueError, match='服务端已接受操作'): online.request('POST', '/api/settings', {}, output=target)
        assert not target.exists()
    finally: online.close()


def test_review_large_json_download_remains_streamable(tmp_path, monkeypatch):
    monkeypatch.setattr('lanbridge.cli.MAX_JSON', 32)
    payload = b'{"test":"' + b'x' * 100 + b'"}'
    target = tmp_path / 'large.json'
    online = OnlineClient('https://example.com', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload, headers={'content-type': 'application/json'})))
    try:
        assert online.request('GET', '/api/state', output=target)['saved_to'] == str(target.resolve())
        assert target.read_bytes() == payload
    finally: online.close()


@pytest.mark.parametrize('runtime', ['null', '[]'])
def test_review_invalid_runtime_root_is_a_friendly_error(tmp_path, capsys, runtime):
    (tmp_path / 'runtime.json').write_text(runtime, encoding='utf-8')
    assert main(['--data-dir', str(tmp_path), 'state']) == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False


@pytest.mark.parametrize('command', ['sites', 'protection'])
def test_review_invalid_state_response_never_causes_traceback(tmp_path, monkeypatch, capsys, command):
    import lanbridge.cli as cli
    original = cli.OnlineClient
    monkeypatch.setenv('LANBRIDGE_TOKEN', 'test-token')
    monkeypatch.setattr(cli, 'OnlineClient', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'null', headers={'content-type': 'application/json'}))))
    assert main(['--profile-dir', str(tmp_path), command]) == 1
    assert '格式无效' in json.loads(capsys.readouterr().out)['error']


@pytest.mark.parametrize('late_action', ['read', 'logout', 'after-local-clear'])
def test_followup_stale_cli_cannot_replace_or_remove_new_session(service, tmp_path, capsys, late_action):
    owner = admin_client(service)
    folder = tmp_path / 'profiles'
    first = OnlineClient('http://127.0.0.1:8890', folder, transport=owner._transport)
    second = None
    try:
        first.request('POST', '/api/login', {'username': 'admin', 'password': 'correct horse battery'})
        if late_action == 'after-local-clear':
            assert main(['--profile-dir', str(folder), 'logout', '--local']) == 0
            assert not first.profile.exists()
        else:
            second = OnlineClient('http://127.0.0.1:8890', folder, transport=owner._transport)
            second.request('POST', '/api/login', {'username': 'admin', 'password': 'correct horse battery'})
            current = first.profile.read_bytes()
        try:
            first.request('POST' if late_action == 'logout' else 'GET', '/api/logout' if late_action == 'logout' else '/api/state', {} if late_action == 'logout' else None)
        except ValueError:
            pass
        if late_action == 'after-local-clear':
            assert not first.profile.exists(), 'A late response must not recreate a cleared session'
        else:
            assert first.profile.exists(), 'Old logout must not delete a newer login'
            assert first.profile.read_bytes() == current, 'Late response must not replace a newer login'
    finally:
        first.close()
        if second: second.close()


def test_followup_unchanged_reads_share_profile_without_rewrites(service, tmp_path):
    owner = admin_client(service)
    first = OnlineClient('http://127.0.0.1:8890', tmp_path / 'profiles', transport=owner._transport)
    second = None
    try:
        first.request('POST', '/api/login', {'username': 'admin', 'password': 'correct horse battery'})
        second = OnlineClient('http://127.0.0.1:8890', tmp_path / 'profiles', transport=owner._transport)
        unchanged = first.profile.read_bytes()
        first.request('GET', '/api/state')
        second.request('GET', '/api/state')
        assert first.profile.read_bytes() == unchanged
    finally:
        first.close()
        if second: second.close()


def test_followup_profile_lock_excludes_other_processes(tmp_path):
    import os
    from lanbridge.cli import profile_lock
    root = Path(__file__).resolve().parents[1]
    profile = tmp_path / 'profile.bin'
    script = 'from pathlib import Path; from lanbridge.cli import profile_lock; import sys; '
    script += '\ntry:\n with profile_lock(Path(sys.argv[1])): sys.exit(5)\nexcept ValueError: sys.exit(0)\n'
    with profile_lock(profile):
        result = subprocess.run([sys.executable, '-B', '-c', script, str(profile)], cwd=root, capture_output=True, timeout=8)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


@pytest.mark.parametrize('arguments', [
    ['change-password', '--password=SYNTHETIC-PRIVATE-VALUE'],
    ['change-password', '--password', 'SYNTHETIC-PRIVATE-VALUE'],
])
def test_output_review_parser_does_not_echo_mistyped_secret(capsys, arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC-PRIVATE-VALUE' not in captured.err
    assert captured.out == ''


def test_output_review_root_parser_does_not_echo_mistyped_secret(capsys):
    from run import main as root_main
    with pytest.raises(SystemExit) as error:
        root_main(['configure-secret', 'SYNTHETIC-PRIVATE-VALUE'])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC-PRIVATE-VALUE' not in captured.err
    assert captured.out == ''


def test_output_review_close_failure_never_reports_valid_download(tmp_path, monkeypatch):
    target = tmp_path / 'close-failed.json'
    original = Path.open
    class CloseFailure:
        def __init__(self, stream): self.stream = stream
        def write(self, chunk): return self.stream.write(chunk)
        def flush(self): self.stream.flush()
        def close(self):
            self.stream.close()
            raise OSError('test close failure')
    def open_target(path, *a, **kw):
        stream = original(path, *a, **kw)
        return CloseFailure(stream) if path == target else stream
    monkeypatch.setattr(Path, 'open', open_target)
    online = OnlineClient('https://example.com', tmp_path / 'profiles', token='test', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'saved': True})))
    try:
        with pytest.raises(ValueError, match='服务端已接受操作'):
            online.request('POST', '/api/settings', {}, output=target)
        assert not target.exists()
    finally: online.close()


def test_final_review_status_does_not_initialize_absent_data(tmp_path, capsys):
    from run import main as root_main
    absent = tmp_path / 'absent'
    assert root_main(['--data-dir', str(absent), 'status']) == 1
    assert not absent.exists()
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_final_review_blocked_offline_write_never_initializes_service(tmp_path, monkeypatch, capsys):
    import run
    def forbidden(root): pytest.fail('Blocked command must not initialize Service/database')
    monkeypatch.setattr(run, 'Service', forbidden)
    with run.acquire_runtime(tmp_path):
        assert run.main(['--data-dir', str(tmp_path), 'configure', str(tmp_path / 'unused.json')]) == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_final_review_status_uses_snapshot_without_service_initialization(service, monkeypatch, capsys):
    import run
    def forbidden(root): pytest.fail('Read-only status must not initialize Service/database')
    monkeypatch.setattr(run, 'Service', forbidden)
    service.store.set('sites', [{'id': 'sample', 'name': 'Sample', 'hostname': 'app.example.com'}])
    assert run.main(['--data-dir', str(service.store.root), 'status']) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['result']['sites'] == service.sites()


def test_final_review_legacy_status_does_not_migrate_database(tmp_path, capsys):
    import sqlite3
    from run import main as root_main
    database = tmp_path / 'state.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT INTO kv VALUES (?,?)', ('settings', '{"tunnel_name":"lanbridge-windows"}'))
    before = database.read_bytes()
    assert root_main(['--data-dir', str(tmp_path), 'status']) == 0
    assert json.loads(capsys.readouterr().out)['result']['settings']['tunnel_name'] == 'lanbridge-windows'
    assert database.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['state.sqlite']


def test_final_review_failed_service_initialization_releases_lock(tmp_path, monkeypatch, capsys):
    import run
    def fail(root): raise ValueError('test initialization failure')
    monkeypatch.setattr(run, 'Service', fail)
    assert run.main(['--data-dir', str(tmp_path), 'configure', 'unused.json']) == 1
    assert not json.loads(capsys.readouterr().out)['ok']
    with run.acquire_runtime(tmp_path): pass


def test_final_review_unlocked_offline_configure_still_works(tmp_path, capsys):
    from run import main as root_main
    from lanbridge.cli_status import read_status
    source = tmp_path / 'config.json'
    source.write_text('{"tunnel_name":"CLI-verified"}', encoding='utf-8')
    data = tmp_path / 'data'
    assert root_main(['--data-dir', str(data), 'configure', str(source)]) == 0
    assert json.loads(capsys.readouterr().out)['ok']
    assert read_status(data)['settings']['tunnel_name'] == 'CLI-verified'
