"""A synthetic session never polls the person's Windows Recent Items."""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from funes_hoard import api as api_module, recent_files
from funes_hoard.hoard_link import LinkConfig
from tests.conftest import agent_headers
from tests.fakes import FakeLink


@pytest.mark.parametrize('demo', [True, False])
def test_only_normal_recording_reads_the_recent_files_source(demo, demo_data_dir, tmp_path, monkeypatch):
    # Make the Windows branch testable on every OS, without installing a
    # native probe or reading any real shortcuts.
    pick = api_module._pick_probe
    monkeypatch.setattr(api_module, '_pick_probe', lambda _: pick(True))
    monkeypatch.setattr(api_module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setenv('HOARD_HUB_URL', 'http://127.0.0.1:1')
    monkeypatch.setenv('HOARD_HUB_AUTOSTART', '0')
    source = tmp_path / 'QA recent source'
    marker = str(tmp_path / 'QA only marker.txt')
    timestamp = time.time() - 1
    discovered, scanned = [], []

    def recent_dir():
        discovered.append(source)
        return source

    def scan(directory, since_ts=0):
        scanned.append(directory)
        return [recent_files.FileEvent(timestamp, marker, 'txt')] if since_ts < timestamp else []

    monkeypatch.setattr(api_module, 'windows_recent_dir', recent_dir)
    monkeypatch.setattr(recent_files, 'scan_once', scan)
    app = api_module.create_app(demo_data_dir, demo=demo, port=18879,
                                link=FakeLink(LinkConfig()), link_factory=FakeLink)
    with TestClient(app, base_url='http://127.0.0.1:18879') as client:
        client.headers.update(agent_headers())
        app.state.scheduler.recent_poller.poll_once()
        reply = client.post('/api/agent/activity_recent_files', json={'since': 'yesterday'}).json()
        if demo:
            assert discovered == scanned == []
            assert not any(item['path'] == marker for item in reply['items'])
            assert app.state.db.query_one('SELECT COUNT(*) n FROM file_events')['n'] > 0
        else:
            assert discovered == [source] and scanned
            assert any(item['path'] == marker for item in reply['items'])
