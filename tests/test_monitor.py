import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as m


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'DB', str(tmp_path / 'test.db'))
    monkeypatch.setattr(m, 'TOKEN', 'test-operator-token')
    monkeypatch.setattr(m, 'demo_failed', False)
    m.states.clear()
    with TestClient(m.app) as c:
        yield c


def test_dashboard_and_health(client):
    assert client.get('/health').json() == {'status': 'ok'}
    assert 'Infrastructure overview' in client.get('/').text
    assert client.get('/static/app.js').status_code == 200
    body = client.get('/api/dashboard').json()
    assert body['services'][0]['status'] == 'pending'
    assert body['events'][0]['type'] == 'startup'


def test_controls_require_token(client):
    assert client.post('/api/check').status_code == 401
    assert client.post('/api/demo/toggle').status_code == 401


def test_disabled_controls(client, monkeypatch):
    monkeypatch.setattr(m, 'TOKEN', '')
    assert client.post('/api/demo/toggle').status_code == 403


@pytest.mark.parametrize('code,latency,expected', [(200,20,'healthy'),(204,1001,'degraded'),(503,10,'unavailable'),(302,10,'unavailable')])
def test_classification(code, latency, expected):
    assert m.classify(code, latency) == expected


def test_probe_http_and_network_errors():
    async def run():
        target = dict(id='api', name='API', url='https://example.test/health', kind='live')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as c:
            result = await m.probe(c, target)
            assert result['status'] == 'unavailable'
            assert 'HTTP 503' in result['error']
        def fail(request):
            raise httpx.ConnectError('connection refused')
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as c:
            result = await m.probe(c, target)
            assert 'ConnectError' in result['error']
    asyncio.run(run())


def test_incident_recovery_persistence(client, monkeypatch):
    status = ['healthy']
    async def probe(c, target):
        return dict(**target, status=status[0], latency=5, error='HTTP 503' if status[0]=='unavailable' else None, checked_at=m.now())
    monkeypatch.setattr(m, 'probe', probe)
    headers = {'Authorization':'Bearer test-operator-token'}
    assert client.post('/api/check', headers=headers).status_code == 200
    status[0] = 'unavailable'
    client.post('/api/check', headers=headers)
    client.post('/api/check', headers=headers)
    status[0] = 'healthy'
    client.post('/api/check', headers=headers)
    data = client.get('/api/dashboard').json()
    assert len([e for e in data['events'] if e['type']=='incident']) == len(m.TARGETS)
    assert len([e for e in data['events'] if e['type']=='recovery']) == len(m.TARGETS)
    assert len(data['metrics']) == 4
    m.initialize()
    assert len(client.get('/api/dashboard').json()['history']) == 4 * len(m.TARGETS)


def test_demo_failure_and_recovery(client, monkeypatch):
    async def noop():
        return []
    monkeypatch.setattr(m, 'check_all', noop)
    headers = {'Authorization':'Bearer test-operator-token'}
    assert client.get('/demo/health').status_code == 200
    assert client.post('/api/demo/toggle', headers=headers).json()['failed']
    assert client.get('/demo/health').status_code == 503
    assert not client.post('/api/demo/toggle', headers=headers).json()['failed']
    assert client.get('/demo/health').status_code == 200


def test_demo_disabled(client, monkeypatch):
    monkeypatch.setattr(m, 'DEMO', False)
    assert client.get('/demo/health').status_code == 404
    assert client.post('/api/demo/toggle', headers={'Authorization':'Bearer test-operator-token'}).status_code == 404
