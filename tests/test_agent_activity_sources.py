"""Transport contracts: no model/DB access and honest source coverage."""
import asyncio
from urllib.parse import parse_qs, urlsplit

from funes_hoard.backend import agent_activity_inputs


def test_reads_tasks_and_advances_through_unfiltered_event_pages(monkeypatch):
    monkeypatch.setenv('HOARD_HUB_URL', 'http://127.0.0.1:1')
    calls = []
    def fetch(url, **kw):
        path, q = urlsplit(url).path, parse_qs(urlsplit(url).query)
        calls.append((path, q, kw))
        if path.endswith('/tasks'):
            return 200, {'ok': True, 'tasks': [{'id': 1}]}, ''
        if path.endswith('/stats'):
            return 200, {'first_ts': 50}, ''
        cursor = int(q['since_id'][0])
        return 200, {'ok': True, 'events': [{'id': n, 'source': 'faustus'} for n in range(1, 2001)] if cursor == 0 else [{'id': 2001}]}, ''
    out = asyncio.run(agent_activity_inputs(100, 200, fetch=fetch))
    assert len(out['events']) == 2001 and out['sources']['bus']['truncated'] is False
    requests = [q for path, q, _ in calls if path.endswith('/events')]
    assert requests[1]['since_id'] == ['2000'] and all('type' not in q for q in requests)
    assert all(kw['method'] == 'GET' and kw['timeout'] <= 2 for _, _, kw in calls)


def test_a_dead_hub_does_not_turn_missing_sources_into_a_clean_empty_day(monkeypatch):
    monkeypatch.setenv('HOARD_HUB_URL', 'http://127.0.0.1:1')
    out = asyncio.run(agent_activity_inputs(100, 200, fetch=lambda *a, **k: (None, None, 'unreachable')))
    assert out['tasks'] == out['events'] == []
    assert out['sources']['agora']['ok'] is out['sources']['bus']['ok'] is False


def test_retention_and_task_cap_are_reported(monkeypatch):
    monkeypatch.setenv('HOARD_HUB_URL', 'http://127.0.0.1:1')
    def fetch(url, **kw):
        path = urlsplit(url).path
        if path.endswith('/tasks'):
            return 200, {'ok': True, 'tasks': [{'id': i} for i in range(500)]}, ''
        if path.endswith('/stats'):
            return 200, {'first_ts': 150}, ''
        return 200, {'ok': True, 'events': []}, ''
    out = asyncio.run(agent_activity_inputs(100, 200, fetch=fetch))
    assert out['sources']['agora']['reason'] == 'limit'
    assert out['sources']['bus']['reason'] == 'retained_window' and out['sources']['bus']['since'] == 150


def test_repeating_page_cannot_loop_or_claim_complete_coverage(monkeypatch):
    monkeypatch.setenv('HOARD_HUB_URL', 'http://127.0.0.1:1')
    seen = []
    def fetch(url, **kw):
        path = urlsplit(url).path;seen.append(path)
        if path.endswith('/tasks'):
            return 200, {'ok': True, 'tasks': []}, ''
        if path.endswith('/stats'):
            return 200, {'first_ts': 0}, ''
        return 200, {'ok': True, 'events': [{'id': i} for i in range(1, 2001)]}, ''
    out = asyncio.run(agent_activity_inputs(100, 200, fetch=fetch))
    assert seen.count('/api/events') == 2
    assert out['sources']['bus']['truncated']
