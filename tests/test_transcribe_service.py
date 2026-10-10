"""The family's speech-to-text service: transcribe_file, transcribe_status, transcribe_cancel (services.md, section 3)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from funes_hoard.api import create_app
from funes_hoard.audio_memory.audio.wav import SAMPLE_RATE, write_wav
from funes_hoard.audio_memory.stt_jobs import NoModel, SttJobs
from funes_hoard.audio_memory.worker import PRIORITY_LIVE, TranscriptionWorker
from funes_hoard.hoard_link.media import stt
from tests.conftest import agent_headers
from tests.fakes import FakeLink

PORT = 18851


@pytest.fixture()
def api(tmp_path, demo_data_dir, monkeypatch):
    monkeypatch.setenv("SCRIBE_AUDIO", "none")
    monkeypatch.setenv("SCRIBE_TRANSCRIBER", "fake")
    app = create_app(demo_data_dir, None, demo=True, port=PORT, link_factory=FakeLink)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        client.headers.update(agent_headers())
        yield client, app.state.audio_app.state.services, tmp_path


def wav(folder, name="clase.wav", seconds=5):
    path = folder / name
    write_wav(path, np.zeros(SAMPLE_RATE * seconds, dtype=np.int16))
    return path


def call(client, name, **arguments):
    return client.post("/api/agent/call", json={"name": name, "arguments": arguments})


def test_transcribe_file_answers_with_the_text_the_segments_and_the_words(api):
    client, svc, tmp = api
    before = svc.sessions.count()
    r = call(client, "transcribe_file", path=str(wav(tmp)), language="es", wait_s=20)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "done" and job["progress"] == 1.0 and len(job["job_id"]) >= 8
    assert job["language"] == "es" and job["duration_s"] == 5.0 and job["model"] and job["device"]
    assert job["text"].startswith("Hola, empezamos") and job["text"] == " ".join(s["text"] for s in job["segments"])
    first = job["segments"][0]
    assert set(first) >= {"start_s", "end_s", "text", "words", "avg_logprob", "no_speech_prob"}
    assert set(first["words"][0]) == {"start_s", "end_s", "word", "p"}
    assert "stats" in job and "note" in job and "language_probability" in job
    assert svc.sessions.count() == before  # stateless: no session, no row


def test_without_wait_only_the_job_fields_are_guaranteed_and_status_returns_the_result(api):
    client, _, tmp = api
    job = call(client, "transcribe_file", path=str(wav(tmp))).json()
    assert {"job_id", "status", "progress"} <= set(job)
    done = call(client, "transcribe_status", job_id=job["job_id"], wait_s=20).json()
    assert done["status"] == "done" and done["segments"] and done["job_id"] == job["job_id"]


def test_bad_arguments_are_400_with_a_reason(api):
    client, _, tmp = api
    folder = tmp / "carpeta.mp3"
    folder.mkdir()
    empty = tmp / "vacio.wav"
    empty.write_bytes(b"")
    text = tmp / "notas.txt"
    text.write_text("hola", encoding="utf-8")
    cases = {"clase.wav": "absolute", str(tmp / "nada.wav"): "No such file", str(folder): "Not a file", str(empty): "empty", str(text): "Unsupported"}
    for path, reason in cases.items():
        r = call(client, "transcribe_file", path=path)
        assert r.status_code == 400 and reason in r.json()["error"], (path, r.text)
    r = call(client, "transcribe_file", path=str(wav(tmp)), language="espanol-latino-1")
    assert r.status_code == 400 and "language" in r.json()["error"]
    r = call(client, "transcribe_file", path=str(wav(tmp)), wait_s=500)
    assert r.status_code == 400


def test_unknown_job_is_404_and_cancel_is_idempotent(api):
    client, _, tmp = api
    r = call(client, "transcribe_status", job_id="nope")
    assert r.status_code == 404 and "no such job: nope" in r.json()["error"]
    assert call(client, "transcribe_cancel", job_id="nope").status_code == 404
    job = call(client, "transcribe_file", path=str(wav(tmp)), wait_s=20).json()
    cancelled = call(client, "transcribe_cancel", job_id=job["job_id"]).json()
    assert cancelled == {"job_id": job["job_id"], "status": "done"}  # a finished job is left as it is
    assert call(client, "transcribe_status", job_id=job["job_id"]).json()["status"] == "done"


def test_a_queued_job_can_be_cancelled_and_never_runs(api):
    client, svc, tmp = api
    gate = threading.Event()
    svc.worker.submit("gate", gate.wait, PRIORITY_LIVE)  # keeps the one worker busy
    time.sleep(0.2)
    job = call(client, "transcribe_file", path=str(wav(tmp))).json()
    assert job["status"] == "queued"
    assert svc.stt.status(job["job_id"], wait_s=0.01)["status"] == "queued"
    assert call(client, "transcribe_cancel", job_id=job["job_id"]).json() == {"job_id": job["job_id"], "status": "cancelled"}
    assert call(client, "transcribe_cancel", job_id=job["job_id"]).json()["status"] == "cancelled"
    gate.set()
    svc.worker.wait_idle(10)
    assert call(client, "transcribe_status", job_id=job["job_id"]).json()["status"] == "cancelled"


def test_no_model_is_a_503_with_its_own_code(api, monkeypatch):
    client, svc, tmp = api
    monkeypatch.setattr(svc.stt, "_available", lambda: False)
    r = call(client, "transcribe_file", path=str(wav(tmp)))
    assert r.status_code == 503 and "faster-whisper" in r.json()["error"]
    with pytest.raises(NoModel):
        svc.stt.submit(str(wav(tmp)))


def test_the_per_tool_route_needs_the_token_and_is_in_the_catalogue(api):
    client, _, tmp = api
    anonymous = {"Authorization": ""}
    assert client.post("/api/agent/transcribe_file", json={"path": str(wav(tmp))}, headers=anonymous).status_code == 401
    ok = client.post("/api/agent/transcribe_file", json={"path": str(wav(tmp)), "wait_s": 20})
    assert ok.status_code == 200 and ok.json()["status"] == "done"
    names = {t["name"]: t for t in client.get("/api/agent/tools").json()["tools"]}
    assert "wait_s" in names["transcribe_file"]["inputSchema"]["properties"]


def test_finished_jobs_are_kept_for_an_hour_and_only_the_newest_fifty(api):
    _, svc, tmp = api
    now = [1000.0]
    svc.stt.clock = lambda: now[0]
    source = wav(tmp, seconds=1)
    ids = []
    for _ in range(53):
        ids.append(svc.stt.submit(str(source), wait_s=20)["job_id"])
        now[0] += 1
    with pytest.raises(LookupError):
        svc.stt.status(ids[0])  # beyond the newest fifty
    assert svc.stt.status(ids[-1])["status"] == "done"
    now[0] += 3601
    with pytest.raises(LookupError):
        svc.stt.status(ids[-1])


def test_the_sessions_and_the_service_share_one_model(api):
    _, svc, tmp = api
    # with the fake transcriber the service has its own fake engine; the real one is the sessions' own instance
    assert svc._stt_engine() is svc._stt_engine()
    from funes_hoard.audio_memory.transcribe.whisper import WhisperTranscriber

    real = WhisperTranscriber(tmp / "models", "tiny", "cpu")
    svc.transcriber = real
    assert svc._stt_engine() is real.engine


class BlockingModel:
    """A faster-whisper stand-in that yields one segment, then waits: the job is running when it is cancelled."""

    started = threading.Event()
    release = threading.Event()

    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio, **_):
        def gen():
            yield SimpleNamespace(start=0.0, end=1.0, text=" primera frase", avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.0, words=[])
            BlockingModel.started.set()
            BlockingModel.release.wait(10)
            yield SimpleNamespace(start=1.0, end=2.0, text=" segunda frase", avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.0, words=[])

        return gen(), SimpleNamespace(language="es", language_probability=0.9, duration=2.0)


def test_a_running_job_is_cancelled_through_the_shared_engine(tmp_path):
    BlockingModel.started.clear()
    BlockingModel.release.clear()
    worker = TranscriptionWorker()
    worker.start()
    engine = stt.Transcriber(tmp_path / "m", "tiny", "cpu", lease=False, model_factory=BlockingModel)
    jobs = SttJobs(worker, lambda: engine, lambda size: engine, lambda: True)
    try:
        job = jobs.submit(str(wav(tmp_path)))
        assert BlockingModel.started.wait(10)
        assert jobs.status(job["job_id"], wait_s=0.02)["status"] == "running"
        assert jobs.cancel(job["job_id"]) == {"job_id": job["job_id"], "status": "cancelled"}
        BlockingModel.release.set()
        assert worker.wait_idle(10)
        assert jobs.status(job["job_id"], wait_s=1)["status"] == "cancelled"
    finally:
        BlockingModel.release.set()
        worker.stop()


def test_a_failing_model_ends_in_error_with_the_message(tmp_path):
    def broken(*a, **k):
        raise RuntimeError("model.bin is corrupt")

    worker = TranscriptionWorker()
    worker.start()
    engine = stt.Transcriber(tmp_path / "m", "tiny", "cpu", lease=False, model_factory=broken)
    jobs = SttJobs(worker, lambda: engine, lambda size: engine, lambda: True)
    try:
        job = jobs.submit(str(wav(tmp_path)), wait_s=10)
        assert job["status"] == "error" and "corrupt" in job["error"]
    finally:
        worker.stop()


def test_an_engine_factory_failure_completes_the_waiting_job(tmp_path):
    def broken_engine():
        raise RuntimeError("engine initialization failed")

    worker = TranscriptionWorker()
    worker.start()
    jobs = SttJobs(worker, broken_engine, broken_engine, lambda: True)
    try:
        job = jobs.submit(str(wav(tmp_path)), wait_s=2)
        assert job["status"] == "error"
        assert "engine initialization failed" in job["error"]
    finally:
        worker.stop()
