"""Federated day query shared by the UI and the read-only agent surface."""
from __future__ import annotations

import time
from datetime import datetime

from funes_hoard import backend
from funes_hoard.core.agent_activity import assemble
from funes_hoard.errors import BadInput
from funes_hoard.timeparse import human_range, parse_day_or_range


async def agent_day(db, day=None, *, limit=50, offset=0, kind=None, now=None, fetch=None):
    if kind not in (None, '', 'turn', 'task', 'commit'):
        raise BadInput('bad_kind', 'kind must be turn, task or commit; omit it for all events.')
    now = time.time() if now is None else now
    start, end = parse_day_or_range(day, None, None, now)
    incoming = await backend.agent_activity_inputs(start, end, fetch=fetch)
    commits = [dict(row) for row in db.query(
        'SELECT ts, repo, sha, subject, author FROM commits WHERE ts >= ? AND ts < ? ORDER BY ts, id LIMIT 5001',
        (start, end))]
    out = assemble(start, end, events=incoming['events'], tasks=incoming['tasks'], commits=commits[:5000],
                   limit=limit, offset=offset, kind=kind)
    sources = {**incoming['sources'], 'git': {'ok': True, 'truncated': len(commits) > 5000, 'reason': 'limit' if len(commits) > 5000 else ''}}
    out.update(day=datetime.fromtimestamp(start).date().isoformat(), human_range=human_range(start, end, now),
               sources=sources, truncated=any(not s['ok'] or s['truncated'] for s in sources.values()))
    return out
