"""Meeting minutes: dates, evidence checks, generation (single pass and map-reduce), storage and the pipeline hook."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

import pytest

from funes_hoard.audio_memory.db import MIGRATIONS, Database
from funes_hoard.audio_memory.due_dates import resolve_due
from funes_hoard.audio_memory.export import render
from funes_hoard.audio_memory.merge import Labelled
from funes_hoard.audio_memory.minutes import (
    MinutesService,
    Transcript,
    merge_action_items,
    parse_json_object,
    render_minutes_md,
    validate_action_items,
)
from funes_hoard.audio_memory.settings import SettingsStore
from funes_hoard.audio_memory.store import SessionStore
from funes_hoard.hoard_link import BackendError, Resolution, Unavailable
from tests.fakes import ScriptedLink

FRIDAY = date(2026, 10, 2)
MEETING_AT = datetime(2026, 10, 2, 10, 0).timestamp()

LINES = [
    (0, 6, "otros", "Buenos días a todos, empezamos con el presupuesto de la reforma."),
    (6, 14, "yo", "Vale, yo me encargo de enviar el presupuesto revisado a Marta el martes."),
    (14, 22, "otros", "Pedro dice: yo preparo el informe de costes para el viernes que viene."),
    (22, 30, "otros", "Decidimos aprobar la opción B de la reforma de la cocina."),
    (30, 38, "yo", "¿Quién habla con el proveedor de azulejos? Queda pendiente."),
]


# --------------------------------------------------------------- due dates --
@pytest.mark.parametrize("text, expected", [
    ("mañana", "2026-10-03"),
    ("pasado mañana", "2026-10-04"),
    ("hoy", "2026-10-02"),
    ("el martes", "2026-10-06"),
    ("el viernes", "2026-10-09"),  # a Friday meeting: the next Friday, not the same day
    ("el viernes por la mañana", "2026-10-09"),
    ("mañana por la mañana", "2026-10-03"),
    ("en dos semanas", "2026-10-16"),
    ("en 3 días", "2026-10-05"),
    ("15 de octubre", "2026-10-15"),
    ("el 3 de enero", "2027-01-03"),
    ("2026-11-05", "2026-11-05"),
    ("14/11/2026", "2026-11-14"),
    ("a finales de mes", "2026-10-31"),
    ("next monday", "2026-10-05"),
    ("la semana que viene", None),
    ("cuando pueda", None),
    ("", None),
    ("30 de febrero", None),
])
def test_resolve_due(text, expected):
    assert resolve_due(text, FRIDAY) == expected


# ------------------------------------------------------------- transcript --
def _segments(lines=LINES):
    return [{"id": i, "start_s": a, "end_s": b, "speaker": s, "text": t} for i, (a, b, s, t) in enumerate(lines, 1)]


def test_quote_must_be_literal_and_evidence_comes_from_the_segments():
    transcript = Transcript(_segments())
    hit = transcript.locate("YO me encargo de enviar el presupuesto revisado a Marta")  # case does not matter
    assert hit["quote"] == "yo me encargo de enviar el presupuesto revisado a Marta"
    assert (hit["start_s"], hit["end_s"], hit["speaker"]) == (6.0, 14.0, "yo")
    assert transcript.locate("[00:06] yo: yo me encargo de enviar el presupuesto")["start_s"] == 6.0  # label and time are stripped
    across = transcript.locate("revisado a Marta el martes. Pedro dice: yo preparo")
    assert across["speaker"] == "otros+yo" and across["start_s"] == 6.0 and across["end_s"] == 22.0
    assert transcript.locate("yo me encargo de pagar la factura") is None
    assert transcript.locate("sí") is None  # too short to prove anything


def test_names_must_be_said_in_the_transcript():
    transcript = Transcript(_segments())
    assert transcript.contains_name("Marta") and transcript.contains_name("Pedro García")
    assert not transcript.contains_name("Lucía")


def test_validate_drops_items_without_evidence_and_blanks_invented_names():
    transcript = Transcript(_segments())
    raw = [
        {"owner": "yo", "action": "Enviar presupuesto revisado", "counterpart": "Marta", "due_text": "el martes", "due_date": "2026-10-07",
         "quote": "yo me encargo de enviar el presupuesto revisado a Marta"},
        {"owner": "Pedro", "action": "Preparar informe de costes", "due_text": "el viernes que viene", "due_date": None,
         "quote": "yo preparo el informe de costes para el viernes que viene"},
        {"owner": "Lucía", "action": "Llamar al fontanero", "quote": "Lucía llamará al fontanero mañana"},  # invented quote
        {"owner": "Lucía", "action": "Aprobar la opción B", "counterpart": "Rodrigo", "quote": "aprobar la opción B de la reforma"},
        {"owner": "yo", "action": "Sin cita", "quote": ""},
        "not a dict",
    ]
    items, dropped = validate_action_items(raw, transcript, FRIDAY)
    assert dropped == 3
    first = next(i for i in items if i["action"].startswith("Enviar"))
    assert first["due_date"] == "2026-10-06"  # words win over the model's date (a Tuesday, not the 7th)
    assert first["evidence"]["speaker"] == "yo" and first["counterpart"] == "Marta"
    blanked = next(i for i in items if i["action"].startswith("Aprobar"))
    assert blanked["owner"] == "" and blanked["counterpart"] == ""  # neither name is in the transcript


def test_a_model_date_before_the_meeting_is_not_a_deadline():
    transcript = Transcript(_segments())
    items, _ = validate_action_items([{"owner": "yo", "action": "Enviar", "due_date": "2024-01-05", "quote": "me encargo de enviar el presupuesto"}], transcript, FRIDAY)
    assert items[0]["due_date"] is None


def test_merge_dedupes_and_orders_by_time():
    a = {"owner": "yo", "action": "Enviar presupuesto", "counterpart": "", "due_date": None, "due_text": "", "evidence": {"start_s": 40, "end_s": 41, "speaker": "yo", "quote": "me encargo del presupuesto"}}
    b = {**a, "action": "Enviar el presupuesto", "evidence": {"start_s": 6, "end_s": 9, "speaker": "yo", "quote": "yo me encargo de enviar"}}
    c = {**a, "action": "enviar presupuesto!"}  # same words as a
    merged = merge_action_items([[a, b], [c]])
    assert [i["evidence"]["start_s"] for i in merged] == [6, 40]


def test_parse_json_object_tolerates_fences_and_chatter():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Aquí tienes: {"a": [1, 2]} listo') == {"a": [1, 2]}
    assert parse_json_object("no json") is None
    assert parse_json_object("[1, 2]") is None


# ---------------------------------------------------------------- service --
@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "scribe.db")
    sessions = SessionStore(db, tmp_path / "sessions")
    settings = SettingsStore(db)
    events: list[tuple[str, dict]] = []
    yield type("Env", (), {"db": db, "sessions": sessions, "settings": settings, "events": events, "tmp": tmp_path})
    db.close()


def make_session(env, lines=LINES, kind="meeting", status="done", title="Reunión de la reforma"):
    session = env.sessions.create(title, kind, True, True, "es", status=status, started_at=MEETING_AT)
    env.sessions.add_segments(session["id"], [Labelled(a, b, s, t) for a, b, s, t in lines], live=False)
    return session["id"]


def service(env, link):
    return MinutesService(env.db, env.sessions, env.settings, (lambda: link) if link is not None else None,
                          now=lambda: 1_700_000_000.0, emit=lambda kind, data: env.events.append((kind, data)))


GOOD = {
    "summary": ["Se revisa el presupuesto de la reforma.", "Se aprueba la opción B."],
    "decisions": ["Aprobar la opción B de la reforma de la cocina"],
    "action_items": [
        {"owner": "yo", "action": "Enviar el presupuesto revisado", "counterpart": "Marta", "due_text": "el martes", "due_date": None,
         "quote": "yo me encargo de enviar el presupuesto revisado a Marta el martes"},
        {"owner": "Pedro", "action": "Preparar el informe de costes", "counterpart": "yo", "due_text": "el viernes que viene",
         "quote": "yo preparo el informe de costes para el viernes que viene"},
        {"owner": "Rodrigo", "action": "Invented task", "quote": "Rodrigo se encarga de todo lo demás"},
    ],
    "open_questions": ["¿Quién habla con el proveedor de azulejos?"],
    "participants": ["Marta", "Pedro", "Fantasma"],
}


def test_generate_stores_checked_minutes_and_announces_them(env):
    sid = make_session(env)
    link = ScriptedLink([GOOD])
    result = service(env, link).generate(sid)
    assert result["status"] == "ready" and result["cached"] is False
    minutes = result["minutes"]
    assert minutes["summary"].splitlines() == GOOD["summary"]
    assert minutes["participants"] == ["Marta", "Pedro"]  # the invented participant is dropped
    assert minutes["model"] == "test-model" and minutes["created_at"] == 1_700_000_000.0
    assert minutes["stats"]["items_dropped_without_evidence"] == 1
    mine, theirs = minutes["action_items"]
    assert (mine["owner"], mine["counterpart"], mine["due_date"], mine["due_text"]) == ("yo", "Marta", "2026-10-06", "el martes")
    assert (theirs["owner"], theirs["counterpart"], theirs["due_date"]) == ("Pedro", "yo", "2026-10-09")
    assert mine["evidence"] == {"start_s": 6.0, "end_s": 14.0, "speaker": "yo", "quote": "yo me encargo de enviar el presupuesto revisado a Marta el martes"}
    # the prompt carries the meeting date and the labelled transcript
    prompt = link.calls[0]["messages"][1]["content"]
    assert "2026-10-02 (viernes)" in prompt and "[00:06] yo: Vale, yo me encargo" in prompt
    assert link.calls[0]["response_format"] == {"type": "json_object"} and link.calls[0]["effort"] in ("low", "medium")
    assert env.events == [("funes.minutes.ready", {
        "minutes_id": sid, "title": "Reunión de la reforma", "date": "2026-10-02", "attendees": ["Marta", "Pedro"],
        "session_id": sid, "action_items": 2, "started_at": "2026-10-02T10:00:00"})]


def test_stored_minutes_are_returned_without_asking_the_model_again(env):
    sid = make_session(env)
    link = ScriptedLink([GOOD])
    svc = service(env, link)
    svc.generate(sid)
    again = svc.generate(sid)
    assert again["cached"] is True and len(link.calls) == 1
    other = {**GOOD, "summary": ["Segunda versión."], "action_items": []}
    link.replies.append(other)
    fresh = svc.generate(sid, regenerate=True)
    assert fresh["minutes"]["summary"] == "Segunda versión." and fresh["minutes"]["action_items"] == []
    assert svc.get(sid)["summary"] == "Segunda versión."  # replaced, not appended


def test_no_model_is_reported_not_invented(env):
    sid = make_session(env)
    nothing = ScriptedLink(resolution=Resolution(capability="llm", provider=None, url=None, model=None, api=None, state="unavailable", reason="nothing loaded", details={}))
    result = service(env, nothing).generate(sid)
    assert result["status"] == "no_model" and "minutes" not in result
    assert service(env, None).generate(sid)["status"] == "no_model"
    assert service(env, nothing).store.get(sid) is None and env.events == []
    assert service(env, nothing).availability()["available"] is False


def test_other_outcomes_are_explicit(env):
    recording = make_session(env, status="processing")
    assert service(env, ScriptedLink()).generate(recording)["status"] == "not_ready"
    silent = make_session(env, lines=[])
    assert service(env, ScriptedLink()).generate(silent)["status"] == "no_speech"
    with pytest.raises(LookupError):
        service(env, ScriptedLink()).generate("nope")
    sid = make_session(env)
    assert service(env, ScriptedLink([BackendError("llamacpp", 500, "boom")])).generate(sid)["status"] == "error"
    broken = service(env, ScriptedLink(["lo siento, no puedo", "tampoco"]))
    result = broken.generate(sid)
    assert result["status"] == "error" and "JSON" in result["detail"]
    assert broken.view(sid)["last"]["status"] == "error"
    assert service(env, ScriptedLink([Unavailable("llm", ["gone"])])).generate(sid)["status"] == "no_model"


def test_one_retry_when_the_model_forgets_json(env):
    sid = make_session(env)
    link = ScriptedLink(["Claro, aquí está el acta:", GOOD])
    assert service(env, link).generate(sid)["status"] == "ready"
    assert len(link.calls) == 2 and "only the JSON" in link.calls[1]["messages"][-1]["content"]


def test_a_long_meeting_is_read_in_slices_then_merged(env):
    lines, moment = [], 0.0
    for n in range(60):
        speaker = "yo" if n % 2 == 0 else "otros"
        lines.append((moment, moment + 18, speaker, f"Punto {n}: hablamos de la partida {n} del proyecto con calma y todo detalle posible. " * 3))
        moment += 20
    lines[5] = (100, 118, "yo", "Quedamos en que yo me encargo de enviar la memoria técnica antes del viernes.")
    lines[45] = (900, 918, "otros", "Ana confirma que ella revisa el contrato con el cliente el lunes.")
    sid = make_session(env, lines=lines)
    transcript = Transcript(env.sessions.segments(sid))
    pieces = transcript.chunks()
    assert len(pieces) >= 2 and sum(len(p) for p in pieces) == 60

    def partial(messages):
        text = messages[1]["content"]
        out = {"summary": ["Parte."], "decisions": ["Decisión común"], "open_questions": [], "participants": ["Ana"], "action_items": []}
        if "enviar la memoria técnica" in text:
            out["action_items"].append({"owner": "yo", "action": "Enviar la memoria técnica", "due_text": "antes del viernes", "quote": "yo me encargo de enviar la memoria técnica antes del viernes"})
        if "Ana confirma" in text:
            out["action_items"].append({"owner": "Ana", "action": "Revisar el contrato", "counterpart": "", "due_text": "el lunes", "quote": "ella revisa el contrato con el cliente el lunes"})
        return out

    reduced = {"summary": [f"Línea {i}" for i in range(12)], "decisions": ["Decisión común"], "open_questions": ["¿Plazos?"], "participants": ["Ana"]}
    link = ScriptedLink([partial] * len(pieces) + [reduced])
    result = service(env, link).generate(sid)
    minutes = result["minutes"]
    assert minutes["stats"]["chunks"] == len(pieces)
    assert len(link.calls) == len(pieces) + 1
    assert [i["action"] for i in minutes["action_items"]] == ["Enviar la memoria técnica", "Revisar el contrato"]
    assert minutes["action_items"][1]["due_date"] == "2026-10-05"
    assert len(minutes["summary"].splitlines()) == 10  # capped
    assert minutes["decisions"] == ["Decisión común"] and minutes["open_questions"] == ["¿Plazos?"]
    assert "Ana" in minutes["participants"]
    assert all(call["effort"] == "low" for call in link.calls[:-1])


def test_if_the_merge_call_fails_the_slice_notes_are_kept(env):
    lines = [(n * 20.0, n * 20.0 + 18, "otros", f"Punto {n}: tema largo número {n} con bastante texto para ocupar sitio en la reunión de hoy." * 2) for n in range(40)]
    sid = make_session(env, lines=lines)
    count = len(Transcript(env.sessions.segments(sid)).chunks())
    part = {"summary": ["Resumen de parte"], "decisions": ["Una decisión"], "open_questions": ["Una pregunta"], "participants": [], "action_items": []}
    link = ScriptedLink([part] * count + ["not json", "still not json"])
    minutes = service(env, link).generate(sid)["minutes"]
    assert minutes["summary"] == "Resumen de parte" and minutes["decisions"] == ["Una decisión"] and minutes["open_questions"] == ["Una pregunta"]


def test_after_transcription_hook_respects_kind_setting_and_length(env):
    link = ScriptedLink([GOOD, GOOD, GOOD])
    svc = service(env, link)
    meeting = make_session(env)
    note = make_session(env, kind="note")
    short = make_session(env, lines=[(0, 3, "yo", "Hola, probando.")])
    for sid in (note, short):
        svc.after_transcription(sid)
    _drain(svc)
    assert link.calls == [] and svc.get(note) is None and svc.get(short) is None

    env.settings.update({"auto_minutes": False})
    svc.after_transcription(meeting)
    _drain(svc)
    assert link.calls == []

    env.settings.update({"auto_minutes": True})
    svc.after_transcription(meeting)
    _drain(svc)
    assert svc.get(meeting)["action_items"] and env.events[-1][0] == "funes.minutes.ready"
    assert svc.is_generating(meeting) is False
    svc.close()


def _drain(svc):
    import time

    deadline = time.time() + 10
    while time.time() < deadline and any(svc.is_generating(s["id"]) for s in svc.sessions.list(limit=50)):
        time.sleep(0.02)


def test_a_second_request_while_one_is_queued_is_not_queued_twice(env):
    sid = make_session(env)
    gate = {}

    def slow(messages):
        import threading

        gate["started"] = True
        gate.setdefault("release", threading.Event()).wait(5)
        return GOOD

    import threading

    gate["release"] = threading.Event()
    svc = service(env, ScriptedLink([slow]))
    assert svc.schedule(sid) is True
    assert svc.schedule(sid) is False
    assert svc.is_generating(sid) is True
    gate["release"].set()
    _drain(svc)
    assert svc.get(sid) is not None
    svc.close()


# ------------------------------------------------------- storage and export --
def test_minutes_are_deleted_with_their_session_and_discarded_on_demand(env):
    sid = make_session(env)
    svc = service(env, ScriptedLink([GOOD]))
    svc.generate(sid)
    assert svc.store.counts([sid]) == {sid: 2}
    svc.discard(sid)
    assert svc.get(sid) is None
    svc.generate(sid, regenerate=True)
    env.sessions.delete(sid)
    assert svc.store.get(sid) is None  # ON DELETE CASCADE


def test_migration_3_adds_the_minutes_table_to_an_existing_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for index in (1, 2):
        conn.executescript(f"BEGIN;\n{MIGRATIONS[index - 1]}\nINSERT INTO schema_version(version) VALUES ({index});\nCOMMIT;")
    conn.execute("INSERT INTO sessions(id, title, started_at) VALUES ('s1', 'Vieja', 1.0)")
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == len(MIGRATIONS) >= 3
        assert db.conn.execute("SELECT title FROM sessions").fetchone()[0] == "Vieja"
        columns = {r["name"] for r in db.conn.execute("PRAGMA table_info(minutes)")}
        assert {"session_id", "summary", "decisions", "action_items", "open_questions", "participants", "model", "created_at"} <= columns
    finally:
        db.close()


def test_markdown_has_the_minutes_with_evidence_and_the_export_puts_them_first(env):
    sid = make_session(env)
    svc = service(env, ScriptedLink([GOOD]))
    svc.generate(sid)
    session, minutes = env.sessions.get(sid), svc.get(sid)
    md = render_minutes_md(session, minutes)
    assert "# Acta: Reunión de la reforma" in md and "## Resumen" in md and "## Acciones" in md
    assert "- [ ] **yo** → Marta: Enviar el presupuesto revisado · para 2026-10-06" in md
    assert "`00:06` yo: «yo me encargo de enviar el presupuesto revisado a Marta el martes»" in md
    assert "## Preguntas abiertas" in md and "Participantes: Marta, Pedro" in md
    full = render("md", session, env.sessions.segments(sid), minutes)
    assert full.index("## Acta") < full.index("### Resumen") < full.index("## Transcripción")
    assert "## Acta" not in render("md", session, env.sessions.segments(sid))  # unchanged without minutes
    assert "Acta" not in render("txt", session, env.sessions.segments(sid), minutes)


def test_settings_have_auto_minutes_on_by_default(env):
    assert env.settings.get().auto_minutes is True
    assert env.settings.update({"auto_minutes": False}).auto_minutes is False
    assert json.loads(env.db.conn.execute("SELECT value FROM settings WHERE key = 'auto_minutes'").fetchone()[0]) is False


# ------------------------------------------------------- who is committing --
SINGLE_TRACK = [
    (0, 8, "S1", "Buenas, repasamos el contrato de la obra."),
    (8, 17, "S1", "Yo me encargo de enviarle el presupuesto a Marta Ficticia el martes que viene."),
    (17, 26, "S1", "Marta Ficticia me tiene que devolver el contrato firmado antes del viernes."),
    (26, 34, "S1", "Pedro Gil prepara el informe de costes para el lunes."),
    (34, 42, "S1", "Luego tú llamas al gremio mañana, ¿vale?"),
    (42, 50, "S1", "I'll send the invoice to Anna tomorrow."),
]


def _item(owner, action, quote, counterpart=None, **extra):
    return {"owner": owner, "action": action, "counterpart": counterpart, "quote": quote, **extra}


def _owners(env, items, lines=SINGLE_TRACK):
    sid = make_session(env, lines)
    link = ScriptedLink([{"summary": ["x"], "decisions": [], "action_items": items, "open_questions": [], "participants": []}])
    result = service(env, link).generate(sid)
    return {i["action"]: (i["owner"], i["counterpart"]) for i in result["minutes"]["action_items"]}


def test_a_first_person_promise_on_a_single_track_is_the_users_even_if_the_model_says_otros(env):
    owners = _owners(env, [
        _item("otros", "Enviar el presupuesto", "Yo me encargo de enviarle el presupuesto a Marta Ficticia el martes que viene", "Marta Ficticia"),
        _item("S1", "Enviar el presupuesto sin contraparte", "me encargo de enviarle el presupuesto a Marta Ficticia"),
        _item("otros", "Mandar la factura", "I'll send the invoice to Anna tomorrow"),
    ])
    assert owners["Enviar el presupuesto"] == ("yo", "Marta Ficticia")
    assert owners["Mandar la factura"] == ("yo", "Anna")  # the counterpart is read from the words when the model gave none
    assert owners["Enviar el presupuesto sin contraparte"] == ("yo", "Marta Ficticia")


def test_another_named_person_as_the_subject_owns_it_and_the_user_is_the_counterpart(env):
    owners = _owners(env, [
        _item("otros", "Devolver el contrato", "Marta Ficticia me tiene que devolver el contrato firmado antes del viernes", "yo"),
        _item("S1", "Devolver el contrato (sin ayuda del modelo)", "Marta Ficticia me tiene que devolver el contrato firmado"),
        _item("otros", "Preparar el informe", "Pedro Gil prepara el informe de costes para el lunes"),
    ])
    assert owners["Devolver el contrato"] == ("Marta Ficticia", "yo")
    assert owners["Devolver el contrato (sin ayuda del modelo)"] == ("Marta Ficticia", "yo")
    assert owners["Preparar el informe"] == ("Pedro Gil", "")


def test_speaker_labels_are_never_owners_or_counterparts(env):
    owners = _owners(env, [
        _item("otros", "Llamar al gremio", "tú llamas al gremio mañana", "S1"),
        _item("S1", "Otra cosa", "Luego tú llamas al gremio", "otros"),
        _item("Speaker 2", "Una más", "llamas al gremio mañana"),
    ])
    assert owners["Llamar al gremio"] == ("", "")
    assert owners["Otra cosa"] == ("", "")
    assert owners["Una más"] == ("", "")


def test_with_a_user_track_someone_elses_first_person_is_not_the_users(env):
    lines = [
        (0, 8, "yo", "Vale, me comprometo a mandar el plan de obra."),
        (8, 16, "otros", "Yo preparo el informe de costes para el viernes."),
        (16, 24, "otros", "Pedro llamará al gremio el lunes."),
    ]
    owners = _owners(env, [
        _item("yo", "Mandar el plan", "me comprometo a mandar el plan de obra"),
        _item("yo", "Preparar el informe", "Yo preparo el informe de costes para el viernes"),
        _item("otros", "Preparar el informe 2", "preparo el informe de costes para el viernes"),
        _item("otros", "Llamar al gremio", "Pedro llamará al gremio el lunes"),
    ], lines)
    assert owners["Mandar el plan"][0] == "yo"
    assert owners["Preparar el informe"][0] == "" and owners["Preparar el informe 2"][0] == ""  # an unnamed other person
    assert owners["Llamar al gremio"][0] == "Pedro"


@pytest.mark.parametrize("quote", [
    "Yo me encargo de enviar el presupuesto", "me encargo del informe", "me comprometo a mandarlo", "me toca llamar al gremio",
    "yo le envío el documento", "te mando el contrato hoy", "les paso las fotos", "yo preparo la memoria", "yo llamaré a Marta",
    "I'll send it tomorrow", "I will call him", "I'm going to write the report",
])
def test_first_person_promises_are_recognised(quote):
    from funes_hoard.audio_memory.minutes import FIRST_PERSON, _ENGLISH_I

    assert FIRST_PERSON.search(quote) or _ENGLISH_I.search(quote)


@pytest.mark.parametrize("quote", ["Marta prepara el informe", "Pedro llamará al gremio", "ella te lo manda mañana", "se envía el lunes"])
def test_third_person_statements_are_not_first_person(quote):
    from funes_hoard.audio_memory.minutes import FIRST_PERSON, _ENGLISH_I

    assert not (FIRST_PERSON.search(quote) or _ENGLISH_I.search(quote))
