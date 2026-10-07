from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from test_security import service


def test_public_client_and_admin_locations_preserve_api_auth(service):
    client = TestClient(create_admin(service), base_url='http://127.0.0.1:8890')
    response = client.get('/', follow_redirects=False)
    assert response.status_code == 307 and response.headers['location'] == '/client'
    assert '转发列表' in client.get('/client').text
    assert 'id="auth-form"' in client.get('/admin').text
    assert client.get('/admin/', follow_redirects=False).headers['location'] == '/admin'
    for path in ('/client.js', '/client.css'):
        assert client.get(path).status_code == 200
    assert client.get('/api/state').status_code == 401
    assert client.post('/api/sites', json={}, headers={'Origin':'http://127.0.0.1:8890'}).status_code == 401
    assert client.get('/api/client/routes', headers={'Host':'evil.example'}).status_code == 403


def test_public_routes_are_limited_to_enabled_mapping_results_and_update_live(service):
    first = service.save_site({'name':'Motor', 'hostname':'motor.example.com', 'origin':'http://127.0.0.1:8765', 'human_check':False})
    service.save_site({'name':'Pending', 'hostname':'pending.example.com', 'origin':'http://127.0.0.1:8766', 'human_check':False})
    service.save_site({'name':'Disabled', 'hostname':'disabled.example.com', 'origin':'http://127.0.0.1:8767', 'human_check':False, 'enabled':False})
    service.store.set('published_hosts', ['motor.example.com', 'disabled.example.com'])
    service.store.set_secret('cf_write_token','private-secret-never-return')
    client = TestClient(create_admin(service), base_url='http://127.0.0.1:8890')
    response = client.get('/api/client/routes')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    routes = response.json()['routes']
    assert len(routes) == 2
    assert routes[0] == {'name':'Motor','hostname':'motor.example.com','origin':'http://127.0.0.1:8765','published':True,'status':'已发布'}
    assert routes[1]['status'] == '未发布' and not routes[1]['published']
    assert 'private-secret' not in response.text and 'account_id' not in response.text and 'passcode' not in response.text
    service.save_site(first | {'origin':'http://127.0.0.1:8768'})
    assert next(row for row in client.get('/api/client/routes').json()['routes'] if row['hostname']==first['hostname'])['origin'].endswith(':8768')
    service.store.audit('publish_incomplete', {})
    assert next(row for row in client.get('/api/client/routes').json()['routes'] if row['hostname']==first['hostname'])['status']=='待核验'
