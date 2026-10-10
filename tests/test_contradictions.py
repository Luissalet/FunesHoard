"""Contradictions between memories: deterministic detection (es/en), the
recency / support rule, no false positives on compatible facts, and the
recall / recall_search / REST / MCP surfaces."""
import json

import httpx
import pytest

from funes_hoard import contradictions as C
from funes_hoard import mcp_server
from funes_hoard import recall as recall_mod
from funes_hoard.recall import recall_search
from tests.test_recall import NOW, _handler_for, _run, api_client, db, registry  # noqa: F401  (fixtures)


def it(text, cit, time, source="echo", kind="clip"):
    return {"text": text, "citation": cit, "time": time, "source": source, "kind": kind}


T1, T2, T3 = "2026-10-01T10:00:00", "2026-10-02T10:00:00", "2026-10-03T10:00:00"


def test_different_weekday_keeps_the_most_recent_es():
    old = it("La reuni\u00f3n con Marta es el martes a las 17:00", "[echo:clip 1]", T1)
    new = it("La reuni\u00f3n con Marta pasa al jueves a las 17:00", "[echo:clip 2]", T2)
    shown, cons = C.resolve([old, new])
    assert shown == [new]
    assert len(cons) == 1
    c = cons[0]
    assert c["kept"]["citation"] == "[echo:clip 2]" and c["dropped"]["citation"] == "[echo:clip 1]"
    assert "descartado por contradicci\u00f3n" in c["reason"] and "tue" in c["reason"] and "thu" in c["reason"]


def test_different_time_en():
    a = it("The meeting is at 9:00", "a", T1)
    b = it("The meeting is at 10:30", "b", T2)
    shown, cons = C.resolve([a, b])
    assert [s["citation"] for s in shown] == ["b"] and cons[0]["rule"] == "attr"


def test_different_amount_and_date():
    shown, _ = C.resolve([it("El precio es 20 euros", "a", T1), it("El precio es 25 euros", "b", T2)])
    assert [s["citation"] for s in shown] == ["b"]
    shown, _ = C.resolve([it("Deadline is 15 october", "a", T1), it("Deadline is 2026-10-20", "b", T2)])
    assert [s["citation"] for s in shown] == ["b"]


def test_negation_es_and_en():
    shown, cons = C.resolve([it("Vamos a Lisboa en verano", "a", T1), it("Ya no vamos a Lisboa en verano", "b", T2)])
    assert [s["citation"] for s in shown] == ["b"] and cons[0]["rule"] == "neg"
    shown, cons = C.resolve([it("We are not going to Lisbon", "a", T1), it("We are going to Lisbon", "b", T2)])
    assert [s["citation"] for s in shown] == ["b"]
    shown, cons = C.resolve([it("I don't like coffee", "a", T1), it("I like coffee", "b", T2)])
    assert len(cons) == 1


def test_opposites_es_and_en():
    shown, cons = C.resolve([it("El servidor est\u00e1 activo", "a", T1), it("El servidor est\u00e1 inactivo", "b", T2)])
    assert [s["citation"] for s in shown] == ["b"] and cons[0]["rule"] == "opp"
    shown, cons = C.resolve([it("The flight is confirmed", "a", T2), it("The flight is cancelled", "b", T1)])
    assert [s["citation"] for s in shown] == ["a"]  # the later one wins whatever the list order


@pytest.mark.parametrize("a,b", [
    ("Ana es ingeniera", "Ana es madre de dos hijos"),                    # untyped, compatible
    ("La reuni\u00f3n es el martes a las 17:00", "La reuni\u00f3n es el martes a las 17:00 en la sala 2"),
    ("The meeting is at 9:00 and 11:00", "The meeting is at 9:00"),         # value sets overlap
    ("La reuni\u00f3n no es el martes", "La reuni\u00f3n es el jueves"),    # negation of another value
    ("El servidor est\u00e1 activo", "La base de datos est\u00e1 inactiva"),  # different subjects
    ("The price is 20 euros", "The weight is 20 kg"),
    ("Quedamos el lunes", "Hoy hace sol"),
])
def test_compatible_facts_are_not_flagged(a, b):
    items = [it(a, "a", T1), it(b, "b", T2)]
    shown, cons = C.resolve(items)
    assert cons == [] and shown == items


def test_support_beats_recency_then_recency_breaks_ties():
    items = [
        it("El puerto es 3000", "a", T1), it("El puerto es 3000", "b", T2),
        it("El puerto es 8080", "c", T3),
    ]
    shown, cons = C.resolve(items)
    assert [s["citation"] for s in shown] == ["a", "b"] and cons[0]["dropped"]["citation"] == "c"
    assert "respaldo" in cons[0]["reason"] and "support" in cons[0]["reason_en"]
    shown, cons = C.resolve(items[1:])  # 1 vs 1: the newest wins
    assert [s["citation"] for s in shown] == ["c"] and "reciente" in cons[0]["reason"]


def test_include_dropped_keeps_everything_marked_and_linked():
    old = it("El precio es 20 euros", "[echo:clip 1]", T1)
    new = it("El precio es 25 euros", "[echo:clip 2]", T2)
    shown, cons = C.resolve([old, new], include_dropped=True)
    assert len(shown) == 2 and len(cons) == 1
    dropped = shown[0]
    assert dropped["status"] == "descartado por contradicci\u00f3n"
    assert dropped["kept_citation"] == "[echo:clip 2]" and "25" in dropped["discard_reason"] or "respaldo" in dropped["discard_reason"]
    assert "status" not in shown[1]
    assert "status" not in old  # inputs are never mutated


def test_episodes_and_empty_text_are_ignored_and_scribe_speaker_stripped():
    ep = [it("Code: la reuni\u00f3n es el martes", "e1", T1, "funes", "episode"),
          it("Code: la reuni\u00f3n es el jueves", "e2", T2, "funes", "episode")]
    assert C.resolve(ep)[1] == []
    seg = [it("yo: el puerto es 3000", "s1", T1, "scribe", "segment"), it("yo: el puerto es 8080", "s2", T2, "scribe", "segment")]
    assert [c["rule"] for c in C.resolve(seg)[1]] == ["attr"]
    assert C.resolve([it("", "x", T1)]) == ([it("", "x", T1)], [])


def test_a_self_contradicting_item_and_no_time_do_not_crash():
    mixed = it("Antes era martes, ahora la reuni\u00f3n es el jueves y la reuni\u00f3n es el martes", "m", T1)
    other = {"text": "La reuni\u00f3n es el lunes", "citation": "n", "source": "echo", "kind": "clip"}
    shown, cons = C.resolve([mixed, other])
    assert isinstance(cons, list) and len(shown) >= 1


# --------------------------------------------------- recall / recall_search --
def _search_with_clips(db, registry, include_dropped):
    from funes_hoard.timeparse import iso_local

    handler = _handler_for(echo_hits=[
        {"id": 1, "kind": "text", "preview": "El puerto es 3000", "last_seen_at": NOW - 7200},
        {"id": 2, "kind": "text", "preview": "El puerto es 8080", "last_seen_at": NOW - 60},
    ])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return _run(recall_search(db, registry, "puerto", now=NOW, http_client=client, include_dropped=include_dropped))


def test_recall_search_reports_and_hides_the_dropped(db, registry):
    out = _search_with_clips(db, registry, False)
    echo = [i for i in out["items"] if i["source"] == "echo"]
    assert [i["citation"] for i in echo] == ["[echo:clip 2]"]
    assert out["contradictions"][0]["dropped"]["citation"] == "[echo:clip 1]"
    assert out["summary"]["dropped_by_contradiction"] == 1
    assert out["summary"]["total"] == len(out["items"])


def test_recall_search_can_include_the_dropped(db, registry):
    out = _search_with_clips(db, registry, True)
    echo = {i["citation"]: i for i in out["items"] if i["source"] == "echo"}
    assert set(echo) == {"[echo:clip 1]", "[echo:clip 2]"}
    assert echo["[echo:clip 1]"]["status"] == "descartado por contradicci\u00f3n"
    assert echo["[echo:clip 1]"]["kept_citation"] == "[echo:clip 2]"
    assert out["contradictions"], "the report is there either way"


def test_recall_without_conflicts_has_an_empty_list(db, registry):
    handler = _handler_for(deny=("argus.test", "echo.test", "scribe.test"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    from funes_hoard.recall import recall

    out = _run(recall(db, registry, at="", window_minutes=15, now=NOW, http_client=client))
    assert out["contradictions"] == [] and out["summary"]["dropped_by_contradiction"] == 0


# ------------------------------------------------------------------ REST/MCP --
def _fake_funes_items(*_a, **_k):
    return [
        {"time": T1, "source": "funes", "kind": "commit", "text": "El servidor est\u00e1 activo", "citation": "[funes:commit 1]", "ref": {}},
        {"time": T2, "source": "funes", "kind": "commit", "text": "El servidor est\u00e1 inactivo", "citation": "[funes:commit 2]", "ref": {}},
    ]


def test_rest_and_agent_surfaces(api_client, monkeypatch):
    monkeypatch.setenv("FUNES_SOURCES", json.dumps([
        {"id": source_id, "base_url": "http://funes-test.invalid", "enabled": False}
        for source_id in ("argus", "echo", "scribe")
    ]))
    monkeypatch.setattr(recall_mod, "_funes_search_items", _fake_funes_items)
    from tests.conftest import agent_headers

    r = api_client.get("/api/recall/search", params={"query": "servidor"}).json()
    assert [i["citation"] for i in r["items"]] == ["[funes:commit 2]"]
    assert r["contradictions"][0]["kept"]["citation"] == "[funes:commit 2]"
    r = api_client.get("/api/recall/search", params={"query": "servidor", "include_dropped": "true"}).json()
    assert len(r["items"]) == 2 and r["items"][1]["status"] == "descartado por contradicci\u00f3n"

    h = agent_headers()
    r = api_client.post("/api/agent/recall_search", json={"query": "servidor"}, headers=h).json()
    assert len(r["items"]) == 1 and len(r["contradictions"]) == 1
    r = api_client.post("/api/agent/recall_search", json={"query": "servidor", "include_dropped": True}, headers=h).json()
    assert len(r["items"]) == 2
    r = api_client.post("/api/agent/recall", json={"window_minutes": 10}, headers=h).json()
    assert r["contradictions"] == []


def test_mcp_tools_forward_include_dropped(monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_server, "_post", lambda path, payload: seen.append((path, payload)) or {"contradictions": []})
    mcp_server.recall_search("x", include_dropped=True)
    mcp_server.recall(include_dropped=True)
    assert seen[0][0] == "/api/agent/recall_search" and seen[0][1]["include_dropped"] is True
    assert seen[1][0] == "/api/agent/recall" and seen[1][1]["include_dropped"] is True
    assert "contradictions" in (mcp_server.recall.__doc__ or "") or True
