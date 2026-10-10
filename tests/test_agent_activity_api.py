"""The normal UI and audited agent routes share one federated projection."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from funes_hoard import backend
from funes_hoard.api import create_app
from tests.conftest import agent_headers
from tests.fakes import FakeLink
from funes_hoard.hoard_link import LinkConfig


@pytest.fixture()
def client(demo_data_dir, monkeypatch):
    async def sources(start, end, **kwargs):
        return {'events': [{'id': 7, 'ts': start + 600, 'source': 'faustus',
                            'type': 'faustus.turn.finished', 'data': {'receipt_id': 'qa', 'session_id': 'own'}}],
                'tasks': [{'id': 3, 'closed_at': start + 900, 'status': 'done', 'owner': 'qa', 'title': 'QA task'}],
                'sources': {'agora': {'ok': True, 'truncated': False, 'reason': ''},
                            'bus': {'ok': True, 'truncated': True, 'reason': 'retained_window', 'since': start + 1}}}
    monkeypatch.setattr(backend, 'agent_activity_inputs', sources)
    app = create_app(demo_data_dir, None, demo=True, port=18833,
                     link=FakeLink(LinkConfig()), link_factory=FakeLink)
    with TestClient(app, base_url='http://127.0.0.1:18833') as c:
        c.headers.update(agent_headers())
        yield c


def test_ui_epochs_agent_iso_and_audited_shared_dispatch(client):
    tools = {tool['name']: tool for tool in client.get('/api/agent/tools').json()['tools']}
    assert tools['activity_agents_day']['annotations']['readOnlyHint'] is True
    assert tools['activity_pause']['annotations']['readOnlyHint'] is False
    ui = client.get('/api/agents/day', params={'day': 'yesterday', 'kind': 'turn'}).json()
    assert ui['total'] == 1 and ui['items'][0]['ref'] == 'turn:own:qa'
    assert isinstance(ui['items'][0]['ts'], (int, float)) and ui['truncated']
    response = client.post('/api/agent/call', json={'name': 'activity_agents_day', 'arguments': {'day': 'yesterday', 'kind': 'turn'}})
    assert response.status_code == 200, response.text
    # The direct route has the same audited handler and preserves source coverage.
    agent = client.post('/api/agent/activity_agents_day', json={'day': 'yesterday', 'kind': 'turn'}).json()
    assert agent['items'][0]['ref'] == ui['items'][0]['ref']
    assert datetime.fromisoformat(agent['start']).utcoffset() is not None
    assert agent['sources']['bus']['reason'] == 'retained_window'
    rows = client.app.state.db.query("SELECT tool, ok FROM agent_calls WHERE tool = 'activity_agents_day'")
    assert len(rows) == 2 and all(row['ok'] for row in rows)


def test_pagination_and_filters(client):
    first = client.get('/api/agents/day', params={'day': 'yesterday', 'limit': 1}).json()
    second = client.get('/api/agents/day', params={'day': 'yesterday', 'limit': 1, 'offset': first['next_offset']}).json()
    assert first['has_more'] and first['items'][0]['ref'] != second['items'][0]['ref']
    tasks = client.get('/api/agents/day', params={'day': 'yesterday', 'kind': 'task'}).json()
    assert tasks['total'] == 1 and tasks['items'][0]['kind'] == 'task'


def test_bad_arguments_are_400_and_agent_requires_token(client):
    for params in ({'kind': 'hours'}, {'limit': 0}, {'offset': -1}, {'day': 'not-a-day'}):
        assert client.get('/api/agents/day', params=params).status_code == 400
    assert client.post('/api/agent/activity_agents_day', json={}, headers={'Authorization': ''}).status_code == 401
