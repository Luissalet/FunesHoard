"""Point-event provenance, time boundaries and pagination without any network."""
from funes_hoard.core.agent_activity import assemble

START, END = 1000.0, 2000.0


def event(eid=1, ts=1100, **data):
    return {"id": eid, "ts": ts, "type": "faustus.turn.finished", "source": "faustus", "data": data}


def task(tid=2, ts=1200, **fields):
    return {"id": tid, "closed_at": ts, "status": "done", "owner": "codex", "title": "Fix the parser", "repo": "Faustus", **fields}


def commit(ts=1300, **fields):
    return {"ts": ts, "repo": "Faustus", "sha": "123abc456def", "subject": "Fix the parser", "author": "Repository author", **fields}


def test_joins_sources_chronologically_and_keeps_their_identity():
    out = assemble(START, END, events=[event(ts=1400)], tasks=[task()], commits=[commit()])
    assert [p['kind'] for p in out['items']] == ['task', 'commit', 'turn']
    assert [p['source'] for p in out['items']] == ['agora', 'git', 'bus']
    assert out['totals'] == {'turn': 1, 'task': 1, 'commit': 1}
    assert out['items'][1]['agent'] == ''  # a Git identity is not an agent
    assert not any('duration' in key for point in out['items'] for key in point)


def test_only_real_turn_types_count_and_payload_contents_are_not_copied():
    secret = 'QA private transcript that must not be returned'
    receipts = [event(prompt=secret, response=secret, title=secret, session_id='s1')]
    receipts += [{**event(2), 'type': 'agent.call'}, {**event(3), 'source': 'other-app'}, event(4, incognito=True)]
    out = assemble(START, END, events=receipts)
    assert out['total'] == 1 and secret not in str(out)
    assert out['items'][0]['session_id'] == 's1'


def test_receipt_retries_and_duplicate_git_rows_are_one_event_each():
    out = assemble(START, END, events=[event(1, receipt_id='r1', session_id='s'), event(2, receipt_id='r1', session_id='s')], commits=[commit(), commit()])
    assert out['total'] == 2
    assert out['totals']['turn'] == out['totals']['commit'] == 1


def test_agreed_producer_identity_and_error_status_survive_projection():
    out = assemble(START, END, events=[event(1, id='s:7', session_id='s', status='error'),
                                     event(2, id='s:7', session_id='s', status='error'),
                                     event(3, id='s:8', session_id='s', status='ok')])
    assert out['total'] == 2
    assert [point['status'] for point in out['items']] == ['failed', 'completed']


def test_range_is_half_open_and_tasks_use_their_close_time_not_update_time():
    out = assemble(START, END, events=[event(1, START), event(2, END)],
                   tasks=[task(updated=9999), task(3, 999, updated=1300), task(4, status='review')])
    assert [p['ref'] for p in out['items']] == ['bus:1', 'agora:2']


def test_missing_or_malformed_receipts_do_not_invent_activity():
    out = assemble(START, END, events=[None, {}, event(ts=float('nan')), event(ts=True), {**event(), 'data': 'not an object'}],
                   tasks=[task(closed_at=None)], commits=[commit(sha='')])
    assert out['items'] == [] and out['total'] == 0


def test_pages_and_kind_filter_keep_the_complete_counts():
    receipts = [event(n, 1100 + n) for n in range(1, 6)]
    first = assemble(START, END, events=receipts, tasks=[task()], kind='turn', limit=2)
    second = assemble(START, END, events=receipts, tasks=[task()], kind='turn', limit=2, offset=first['next_offset'])
    assert first['total'] == 5 and first['totals']['task'] == 1
    assert first['has_more'] and second['next_offset'] == 4
    assert set(p['ref'] for p in first['items']).isdisjoint(p['ref'] for p in second['items'])


def test_metadata_is_plain_text_and_cannot_replace_the_fixed_turn_title():
    out = assemble(START, END, tasks=[task(title='  A\n multiline\t title  ')], events=[event(status='QA arbitrary payload')])
    assert out['items'][0]['title'] == 'Faustus turn' and out['items'][0]['status'] == 'finished'
    assert out['items'][1]['title'] == 'A multiline title'
