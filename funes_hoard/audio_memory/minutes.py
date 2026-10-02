"""Meeting minutes (acta): summary, decisions, action items and open questions of one transcribed session.

The local language model (through Hoard Link, `app.state.link` of the host app)
reads the transcript and proposes the minutes; this module then **checks** what
it proposed against the transcript, so a model slip never reaches the user as
fact:

* every action item carries a quote, and the quote must be a literal piece of
  the transcript (compared without caring about case or spacing); the stored
  quote is the transcript's own text and the time and speaker come from the
  segments that hold it, never from the model. Items whose quote is not found
  are dropped and counted;
* names (owner, counterpart, participants) must appear in the transcript, or
  they are blanked / dropped;
* a due date is only kept when it is a valid day; spoken words that name one
  ("el martes") are resolved here, from the day of the meeting, not by the model.

Long transcripts are cut by time (map), each piece is read on its own, and a
final call merges summaries, decisions and questions (reduce); action items are
merged without a model so their evidence is never rewritten.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Callable

from .db import Database
from .due_dates import resolve_due
from .export import hms
from .settings import SettingsStore
from .store import SessionStore
from .timeparse import iso_local

log = logging.getLogger("scribe.minutes")

AUTO_KINDS = ("meeting", "interview")
AUTO_MIN_CHARS = 200
SINGLE_PASS_CHARS = 14000
CHUNK_SECONDS = 600.0
CHUNK_CHARS = 9000
MAX_ACTION_ITEMS = 60
MAX_LIST_ITEMS = 30
MAX_SUMMARY_LINES = 10
MAX_TOKENS = 3000
OWNER_ME = "yo"
OWNER_OTHERS = "otros"
_ME_WORDS = {"yo", "i", "me", "myself", "mi", "yo mismo", "yo misma"}
_OTHERS_WORDS = {"otros", "otro", "otra", "others", "other", "ellos"}

SYSTEM_PROMPT = (
    "You write meeting minutes from a speech-recognition transcript. Lines look like "
    "`[mm:ss] speaker: text`; the speaker `yo` is the user (microphone), `otros` is everyone else (system audio, may "
    "be several people) and `S1` means one track with no speaker information. Recognition can contain errors.\n"
    "Rules: write in the language of the transcript. Use only what the transcript says; never invent a person, a "
    "task, a date or a decision. An action item is something somebody explicitly commits to do (or is asked to do "
    "and accepts). For each one give `quote`: the exact words from the transcript that show the commitment, copied "
    "character by character from one or two consecutive lines, without the timestamp or the speaker label. "
    "`owner` is `yo` when the user commits (the user says \"me encargo\", \"te lo mando\", \"I'll send\"; on a single "
    "track `S1` a first-person promise is the user's), otherwise the name of the person as spoken, or null when "
    "nobody is named. Never use a speaker label (`otros`, `S1`, `S2`) as an owner or counterpart. `counterpart` is who it "
    "is for, if said (`yo` when the other side promises something to the user). `due_text` is the deadline in the "
    "words used (\"el viernes\"), `due_date` only if it is an exact calendar day you can compute from the meeting "
    "date given, otherwise null. Return a single JSON object and nothing else."
)

SCHEMA_HINT = (
    '{"summary": ["short line", "..."], "decisions": ["..."], "action_items": [{"owner": "yo|name|null", '
    '"action": "what will be done", "counterpart": "name or null", "due_date": "YYYY-MM-DD or null", '
    '"due_text": "words used or null", "quote": "exact words from the transcript"}], '
    '"open_questions": ["..."], "participants": ["names mentioned"]}'
)

REDUCE_PROMPT = (
    "You merge partial meeting notes into one set. Input: JSON with one entry per time slice, each with `summary`, "
    "`decisions`, `open_questions` and `participants`. Write in the same language. Return a JSON object "
    '{"summary": [5 to 10 short lines covering the whole meeting in order], "decisions": [deduplicated], '
    '"open_questions": [deduplicated; drop one only if another entry clearly answers it], "participants": '
    "[deduplicated]}. Use only what the entries say; add nothing. Nothing else."
)

WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


class MinutesError(RuntimeError):
    """The model answered something unusable or failed; `detail` says what, for the user."""


# ---------------------------------------------------------------- text helpers --
def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _norm_key(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower()).strip()


def _str_list(value: Any, limit: int = MAX_LIST_ITEMS) -> list[str]:
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in value if isinstance(value, list) else []:
        text = _squash(str(item)) if isinstance(item, (str, int, float)) else ""
        key = _norm_key(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text[:400])
        if len(out) >= limit:
            break
    return out


def parse_json_object(raw: str) -> dict | None:
    """The first JSON object in a model answer (tolerates code fences and chatter around it)."""
    text = (raw or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


# ------------------------------------------------------------ transcript index --
class Transcript:
    """Segments as prompt text plus an index to find where a quote sits."""

    def __init__(self, segments: list[dict]):
        self.segments = [s for s in segments if _squash(s.get("text", ""))]
        self.lines = [f"[{hms(s['start_s'])}] {s['speaker']}: {_squash(s['text'])}" for s in self.segments]
        self._joined = ""
        self._spans: list[tuple[int, int, dict]] = []
        parts = []
        cursor = 0
        for seg in self.segments:
            text = _squash(seg["text"])
            self._spans.append((cursor, cursor + len(text), seg))
            parts.append(text)
            cursor += len(text) + 1
        self._joined = " ".join(parts)
        lowered = self._joined.lower()
        # str.lower() can change the length of a few characters; then only exact matches are trusted.
        self._lower = lowered if len(lowered) == len(self._joined) else None

    @property
    def speakers(self) -> set[str]:
        return {seg["speaker"] for seg in self.segments}

    @property
    def chars(self) -> int:
        return sum(len(line) + 1 for line in self.lines)

    def contains_name(self, name: str) -> bool:
        """True when the name, or one of its words (3+ letters), is said in the transcript."""
        text = self._joined.lower()
        needle = _squash(name).lower()
        if not needle:
            return False
        if needle in text:
            return True
        return any(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text) for token in re.findall(r"\w{3,}", needle))

    def _find(self, wanted: str) -> int:
        index = self._joined.find(wanted)
        if index >= 0:
            return index
        if self._lower is not None and len(wanted.lower()) == len(wanted):
            return self._lower.find(wanted.lower())
        return -1

    def locate(self, quote: str) -> dict | None:
        """Evidence for a quote: its literal transcript text plus time and speaker of the lines holding it."""
        wanted = _squash(quote).strip(" \"'“”«»…")
        wanted = re.sub(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(?:yo|otros|S\d+)?:?\s*", "", wanted, flags=re.IGNORECASE)
        wanted = wanted.strip(" \"'“”«»…")
        if len(wanted) < 6:
            return None
        index = self._find(wanted)
        if index < 0:
            return None
        end = index + len(wanted)
        hit = [seg for (a, b, seg) in self._spans if a < end and b > index]
        if not hit:
            return None
        speakers = {seg["speaker"] for seg in hit}
        return {
            "start_s": round(float(hit[0]["start_s"]), 2),
            "end_s": round(float(hit[-1]["end_s"]), 2),
            "speaker": hit[0]["speaker"] if len(speakers) == 1 else "+".join(sorted(speakers)),
            "quote": self._joined[index:end],
        }

    def chunks(self, seconds: float = CHUNK_SECONDS, chars: int = CHUNK_CHARS) -> list[list[str]]:
        """Lines grouped by time (and a size cap); each group is read on its own."""
        groups: list[list[str]] = []
        current: list[str] = []
        used, origin = 0, None
        for seg, line in zip(self.segments, self.lines):
            if origin is None:
                origin = seg["start_s"]
            if current and (seg["start_s"] - origin >= seconds or used + len(line) > chars):
                groups.append(current)
                current, used, origin = [], 0, seg["start_s"]
            current.append(line)
            used += len(line) + 1
        if current:
            groups.append(current)
        return groups


# ------------------------------------------------------------------ validation --
_LABEL = re.compile(r"^(?:s\d+|speaker\s*\d*|hablante\s*\d*|spk\s*\d*|track\s*\d*|pista\s*\d*)$", re.IGNORECASE)
_NULLISH = {"null", "none", "unknown", "desconocido", "n/a", "nadie", "alguien", "someone", "nobody", "anyone"}


def _clean_name(value: Any, transcript: Transcript, allow_me: bool = True) -> str:
    """A person as the minutes may name one: "yo", or a name said in the transcript. Speaker labels are never people."""
    text = _squash(str(value or ""))[:80]
    low = text.lower()
    if low in _ME_WORDS:
        return OWNER_ME if allow_me else ""
    if not text or low in _NULLISH or low in _OTHERS_WORDS or _LABEL.match(text):
        return ""
    if low in {s.lower() for s in transcript.speakers}:
        return ""
    return text if transcript.contains_name(text) else ""


def _clean_owner(value: Any, transcript: Transcript) -> str:  # kept for callers that only need the plain rule
    return _clean_name(value, transcript)


# Who is speaking in a quote, worked out from its words. The model tends to copy speaker labels ("otros", "S1")
# into the owner, so what the words say wins over what the model answered.
_NAME = r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]+(?:\s+(?:(?:de|del|la)\s+)?[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]+)*"
_PROMISE_VERBS = (
    r"env[ií]o|enviar[eé]|mando|mandar[eé]|paso|pasar[eé]|llamo|llamar[eé]|escribo|escribir[eé]|preparo|preparar[eé]|"
    r"traigo|traer[eé]|devuelvo|devolver[eé]|entrego|entregar[eé]|aviso|avisar[eé]|confirmo|confirmar[eé]|doy|dar[eé]|"
    r"reviso|revisar[eé]|hago|har[eé]|pago|pagar[eé]|pido|pedir[eé]|busco|buscar[eé]|redacto|presento|organizo|reservo|contacto"
)
FIRST_PERSON = re.compile(
    r"(?<!\w)(?:"
    r"(?:yo\s+)?me\s+(?:encargo|comprometo|toca|ocupo|hago\s+cargo|pongo|apunto|llevo|quedo|voy)\b"
    rf"|(?:yo\s+)?(?:se\s+lo\s+|se\s+la\s+|lo\s+|la\s+|le\s+|te\s+|les\s+|os\s+|nos\s+)(?:{_PROMISE_VERBS})\b"
    rf"|yo\s+(?:voy\s+a|(?:{_PROMISE_VERBS})|\w+é)\b"
    r")",
    re.IGNORECASE,
)
# English "I" must be a capital I, so it is checked apart from the case-insensitive Spanish pattern.
_ENGLISH_I = re.compile(r"\bI(?:'ll|\s+will|\s+shall|'m\s+going\s+to|\s+am\s+going\s+to|\s+can\s+take|\s+have\s+to|\s+need\s+to|\s+must)\b")
_NOT_NAMES = {
    "yo", "vale", "bueno", "entonces", "pues", "si", "sí", "que", "el", "la", "los", "las", "un", "una", "hay", "hoy", "mañana",
    "luego", "después", "primero", "y", "pero", "así", "eso", "esto", "lo", "se", "me", "te", "no", "ok", "okay", "tú", "usted",
    "nosotros", "ellos", "ella", "él", "i", "we", "the", "then", "so", "well", "tomorrow", "today", "also", "and", "but", "he", "she",
    "they", "you", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre", "muy", "ya", "ahora", "bien", "gracias", "perfecto", "claro",
}
_PREDICATE = re.compile(
    r"^\s*(?:,\s*)?(?:me|nos|te|le|les|se|va|vamos|van|tiene|tienen|debe|deben|puede|pueden|quiere|quieren|ha|han|ya|también|"
    r"dijo|dice|prometió|promete|\w+(?:ará|erá|irá|aré)|will|would|is\s+going|has\s+to|needs\s+to|must|should|can|'ll)\b"
    r"|^\s*\w{4,}(?:a|e|an|en)\s+(?:el|la|los|las|un|una|lo|su|mis|mi|este|esta|todo)\b",
    re.IGNORECASE,
)
_PREPOSITIONS = {"a", "para", "con", "de", "del", "por", "en", "sobre", "hacia", "desde", "le", "la", "al", "to", "for", "with", "from", "by"}


def _speaks_as_user(speaker: str, transcript: Transcript) -> bool:
    """A first-person promise is the user's when the line is the user's, or when there is only one track to tell apart."""
    return speaker == OWNER_ME or len(transcript.speakers) <= 1


def _subject_name(quote: str) -> str:
    """A capitalised name that is the subject of the sentence ("Marta Ficticia me tiene que devolver..."), or ""."""
    for match in re.finditer(_NAME, quote):
        name = match.group(0)
        words = name.split()
        while words and words[0].lower() in _NOT_NAMES:
            words.pop(0)
        if not words:
            continue
        name = " ".join(words)
        before = quote[:match.start()].rstrip()
        previous = re.findall(r"[\wáéíóúñü']+|[,.;:]", before)[-1:] or [""]
        if previous[0].lower() in _PREPOSITIONS:
            continue
        if _PREDICATE.search(quote[match.end():]):
            return name
    return ""


def _object_name(quote: str) -> str:
    """A capitalised name the promise is for ("... a Marta el martes"), or ""."""
    for match in re.finditer(rf"(?<![\wáéíóúñü])(?:a|para|con|to|for|with)\s+({_NAME})", quote):
        words = match.group(1).split()
        while words and words[0].lower() in _NOT_NAMES:
            words.pop(0)
        if words:
            return " ".join(words)
    return ""


def infer_owner(entry: dict, evidence: dict, transcript: Transcript) -> tuple[str, str]:
    """(owner, counterpart) for an action item. Words beat labels: see the notes above."""
    quote = evidence["quote"]
    speaker = evidence["speaker"]
    model_owner = _clean_name(entry.get("owner"), transcript)
    model_counterpart = _clean_name(entry.get("counterpart"), transcript)
    me_ok = all(_speaks_as_user(part, transcript) for part in speaker.split("+"))
    first = bool(FIRST_PERSON.search(quote) or _ENGLISH_I.search(quote))
    if first and me_ok:
        counterpart = model_counterpart if model_counterpart not in ("", OWNER_ME) else _object_name(quote)
        return OWNER_ME, counterpart if counterpart != OWNER_ME else ""
    subject = _subject_name(quote)
    if subject:
        aimed_at_me = bool(re.search(r"\b(?:me|nos)\s+(?:\w+\s+){0,3}?(?:tiene|tienen|debe|deben|va|van|puede|pueden|\w+(?:ará|erá|irá|aré))", quote, re.IGNORECASE)) \
            or bool(re.search(r"\b(?:me|nos)\s+\w+", quote[quote.find(subject) + len(subject):quote.find(subject) + len(subject) + 12], re.IGNORECASE))
        counterpart = OWNER_ME if aimed_at_me or model_counterpart == OWNER_ME else (model_counterpart if model_counterpart.lower() != subject.lower() else "")
        return subject, counterpart
    owner = model_owner
    if first and not me_ok and owner == OWNER_ME:
        owner = ""  # somebody else's "I": the user did not say it
    counterpart = model_counterpart
    if counterpart and counterpart.lower() == owner.lower():
        counterpart = ""
    return owner, counterpart


def validate_action_items(raw: Any, transcript: Transcript, meeting_day: date) -> tuple[list[dict], int]:
    """Keep only items whose quote is in the transcript; fix evidence and dates. Returns (items, dropped)."""
    items: list[dict] = []
    dropped = 0
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        action = _squash(str(entry.get("action") or entry.get("task") or ""))[:300]
        evidence = transcript.locate(str(entry.get("quote") or entry.get("evidence") or ""))
        if not action or evidence is None:
            dropped += 1
            continue
        due_text = _squash(str(entry.get("due_text") or ""))[:120]
        if due_text.lower() in ("null", "none"):
            due_text = ""
        due_date = None
        resolved = resolve_due(due_text, meeting_day) if due_text else None
        claimed = str(entry.get("due_date") or "").strip()
        if resolved:
            due_date = resolved
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", claimed):
            try:
                due_date = datetime.strptime(claimed, "%Y-%m-%d").date().isoformat()
            except ValueError:
                due_date = None
        # A model date earlier than the meeting is a misreading of the year, not a deadline.
        if due_date and due_date < meeting_day.isoformat():
            due_date = None
        owner, counterpart = infer_owner(entry, evidence, transcript)
        items.append({
            "owner": owner,
            "action": action,
            "counterpart": counterpart,
            "due_date": due_date,
            "due_text": due_text,
            "evidence": evidence,
        })
    return items, dropped


def merge_action_items(groups: list[list[dict]]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for group in groups:
        for item in group:
            key = (_norm_key(item["owner"]), _norm_key(item["action"]))
            quote_key = ("quote", _norm_key(item["evidence"]["quote"]))
            if key in seen or quote_key in seen:
                continue
            seen.add(key)
            seen.add(quote_key)
            merged.append(item)
    merged.sort(key=lambda it: (it["evidence"]["start_s"], it["action"]))
    return merged[:MAX_ACTION_ITEMS]


def _summary_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part for part in re.split(r"\n+", value) if part.strip()]
    return _str_list(value, MAX_SUMMARY_LINES)


# ------------------------------------------------------------- family shapes --
def attendees_of(minutes: dict) -> list[str]:
    """Names of the people who were in the meeting as far as the minutes know: the participants the transcript names,
    plus every other person an action item names (owner or counterpart). The user ("yo") is not listed."""
    seen: set[str] = set()
    names: list[str] = []
    candidates = list(minutes.get("participants") or [])
    for item in minutes.get("action_items") or []:
        candidates += [item.get("owner") or "", item.get("counterpart") or ""]
    for raw in candidates:
        name = _squash(str(raw or ""))[:80]
        key = name.lower()
        if not name or key in _ME_WORDS or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names[:MAX_LIST_ITEMS]


def _day_of(started_at: float | None) -> str:
    return datetime.fromtimestamp(started_at).date().isoformat() if started_at else ""


def ready_event(session: dict, minutes: dict) -> dict:
    """Data of `funes.minutes.ready`. `minutes_id` is the session id (what `minutes_get` takes); `session_id`, `action_items`
    (a count) and `started_at` are kept for the consumers written before the family contract named it."""
    return {
        "minutes_id": session["id"],
        "title": session["title"][:120],
        "date": _day_of(session.get("started_at")),
        "attendees": attendees_of(minutes),
        "session_id": session["id"],
        "action_items": len(minutes.get("action_items") or []),
        "started_at": iso_local(session["started_at"]),
    }


def family_view(session_id: str, minutes: dict) -> dict:
    """The minutes in the shape other apps ask for (`minutes_get`): {title, date, attendees, action_items: [{text, owner, due...}], summary}."""
    items = []
    for item in minutes.get("action_items") or []:
        ev = item.get("evidence") or {}
        items.append({
            "text": item["action"], "owner": item.get("owner") or "", "counterpart": item.get("counterpart") or "",
            "due": item.get("due_date") or None, "due_text": item.get("due_text") or "",
            "quote": ev.get("quote", ""), "t": hms(ev.get("start_s", 0)),
        })
    return {
        "minutes_id": session_id, "title": minutes.get("title", ""), "date": _day_of(minutes.get("started_at")),
        "attendees": attendees_of(minutes), "action_items": items, "summary": minutes.get("summary", ""),
        "decisions": minutes.get("decisions") or [], "open_questions": minutes.get("open_questions") or [],
    }


# --------------------------------------------------------------------- storage --
class MinutesStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, session_id: str) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM minutes WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "summary": row["summary"],
            "decisions": json.loads(row["decisions"] or "[]"),
            "action_items": json.loads(row["action_items"] or "[]"),
            "open_questions": json.loads(row["open_questions"] or "[]"),
            "participants": json.loads(row["participants"] or "[]"),
            "model": row["model"],
            "created_at": row["created_at"],
            "stats": json.loads(row["stats"] or "{}"),
        }

    def save(self, minutes: dict) -> None:
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO minutes(session_id, summary, decisions, action_items, open_questions, participants, model, created_at, stats)"
                " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET summary = excluded.summary,"
                " decisions = excluded.decisions, action_items = excluded.action_items, open_questions = excluded.open_questions,"
                " participants = excluded.participants, model = excluded.model, created_at = excluded.created_at, stats = excluded.stats",
                (
                    minutes["session_id"], minutes["summary"], json.dumps(minutes["decisions"], ensure_ascii=False),
                    json.dumps(minutes["action_items"], ensure_ascii=False), json.dumps(minutes["open_questions"], ensure_ascii=False),
                    json.dumps(minutes["participants"], ensure_ascii=False), minutes["model"], minutes["created_at"],
                    json.dumps(minutes.get("stats", {})),
                ),
            )

    def delete(self, session_id: str) -> bool:
        with self.db.lock:
            return self.db.conn.execute("DELETE FROM minutes WHERE session_id = ?", (session_id,)).rowcount > 0

    def counts(self, session_ids: list[str]) -> dict[str, int]:
        """Action items per session for the list view."""
        if not session_ids:
            return {}
        marks = ",".join("?" for _ in session_ids)
        with self.db.lock:
            rows = self.db.conn.execute(f"SELECT session_id, json_array_length(action_items) AS n FROM minutes WHERE session_id IN ({marks})", session_ids).fetchall()
        return {r["session_id"]: r["n"] for r in rows}


# --------------------------------------------------------------------- service --
class MinutesService:
    """Generate, store and announce minutes. `link_provider` returns the host's Hoard Link (or None)."""

    def __init__(self, db: Database, sessions: SessionStore, settings: SettingsStore, link_provider: Callable[[], Any] | None = None,
                 now: Callable[[], float] = time.time, emit: Callable[..., Any] | None = None):
        self.store = MinutesStore(db)
        self.sessions = sessions
        self.settings = settings
        self.link_provider = link_provider
        self.now = now
        self.emit = emit
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._active: set[str] = set()
        self._queued: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scribe-minutes")
        self._last: dict[str, dict] = {}  # last non-ready outcome per session, for the UI

    # ----- availability and state -----
    def _link(self):
        try:
            return self.link_provider() if self.link_provider else None
        except Exception:  # noqa: BLE001
            return None

    def availability(self) -> dict:
        link = self._link()
        if link is None:
            return {"available": False, "detail": "No Hoard Link is configured in this app."}
        try:
            res = link.sync.resolve("llm")
        except Exception as error:  # noqa: BLE001
            return {"available": False, "detail": f"{type(error).__name__}: {str(error)[:160]}"}
        if getattr(res, "resolved", False):
            return {"available": True, "model": getattr(res, "model", None), "detail": str(getattr(res, "reason", ""))[:200]}
        return {"available": False, "detail": str(getattr(res, "reason", "no language model resolved"))[:200]}

    def is_generating(self, session_id: str) -> bool:
        with self._guard:
            return session_id in self._active or session_id in self._queued

    def get(self, session_id: str) -> dict | None:
        minutes = self.store.get(session_id)
        if minutes is None:
            return None
        session = self.sessions.get(session_id) or {}
        return {**minutes, "title": session.get("title", ""), "started_at": session.get("started_at")}

    def view(self, session_id: str) -> dict:
        """What the UI shows: the minutes (or null), whether one is being written, and whether a model exists."""
        minutes = self.get(session_id)
        state = {"minutes": minutes, "generating": self.is_generating(session_id), "last": self._last.get(session_id)}
        if minutes is None:
            state["model"] = self.availability()
        return state

    def discard(self, session_id: str) -> None:
        self.store.delete(session_id)

    # ----- generation -----
    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def generate(self, session_id: str, regenerate: bool = False, automatic: bool = False) -> dict:
        """Minutes for a transcribed session. Returns {status, ...}; never raises for "no model"."""
        outcome = self._generate(session_id, regenerate, automatic)
        if outcome.get("status") in ("ready", "too_short"):
            self._last.pop(session_id, None)
        else:
            self._last[session_id] = {"status": outcome.get("status"), "detail": outcome.get("detail", "")}
        return outcome

    def _generate(self, session_id: str, regenerate: bool, automatic: bool) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise LookupError(f"Unknown session: {session_id}")
        if session["status"] != "done":
            return {"status": "not_ready", "session_status": session["status"], "detail": "The session is not transcribed yet; try again when its status is done."}
        with self._lock_for(session_id):
            existing = self.store.get(session_id)
            if existing is not None and not regenerate:
                return {"status": "ready", "cached": True, "minutes": self.get(session_id)}
            transcript = Transcript(self.sessions.segments(session_id))
            if not transcript.segments:
                return {"status": "no_speech", "detail": "The session has no transcribed speech, so there is nothing to write minutes from."}
            if automatic and transcript.chars < AUTO_MIN_CHARS:
                return {"status": "too_short", "detail": "The transcript is too short for automatic minutes; generate them by hand if you want them."}
            link = self._link()
            if link is None:
                return {"status": "no_model", "detail": "No Hoard Link is configured in this app."}
            with self._guard:
                self._active.add(session_id)
            try:
                result = self._write(session, transcript, link)
            except _NoModel as error:
                return {"status": "no_model", "detail": str(error)}
            except MinutesError as error:
                return {"status": "error", "detail": str(error)}
            finally:
                with self._guard:
                    self._active.discard(session_id)
            self.store.save(result)
        if self.emit:
            try:
                self.emit("funes.minutes.ready", ready_event(session, result))
            except Exception:  # noqa: BLE001 - events are hints
                log.debug("minutes event not sent", exc_info=True)
        return {"status": "ready", "cached": False, "minutes": self.get(session_id)}

    def schedule(self, session_id: str, regenerate: bool = False, automatic: bool = False) -> bool:
        """Run generation in the background (one at a time); False when it is already queued or running."""
        with self._guard:
            if session_id in self._queued:
                return False
            self._queued.add(session_id)

        def job() -> None:
            try:
                self.generate(session_id, regenerate=regenerate, automatic=automatic)
            except Exception:  # noqa: BLE001 - a failing job must not kill the pool thread
                log.exception("minutes for %s failed", session_id)
            finally:
                with self._guard:
                    self._queued.discard(session_id)

        try:
            self._pool.submit(job)
        except RuntimeError:  # the pool is closed: the app is shutting down
            with self._guard:
                self._queued.discard(session_id)
            return False
        return True

    def after_transcription(self, session_id: str) -> None:
        """Pipeline hook: a session finished transcribing; write minutes in the background if the user wants them."""
        session = self.sessions.get(session_id)
        if session is None or session["kind"] not in AUTO_KINDS or session["status"] != "done":
            return
        if not self.settings.get().auto_minutes:
            return
        self.schedule(session_id, automatic=True)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ----- the model passes -----
    def _chat(self, link, messages: list[dict], effort: str) -> tuple[str, str]:
        from funes_hoard.hoard_link import BackendError, Unavailable

        try:
            result = link.sync.chat(messages, max_tokens=MAX_TOKENS, temperature=0.1, response_format={"type": "json_object"}, effort=effort)
        except Unavailable as error:
            raise _NoModel("; ".join(error.reasons) or "no language model is available") from error
        except BackendError as error:
            raise MinutesError(f"The language model call failed: {error}") from error
        except Exception as error:  # noqa: BLE001
            raise MinutesError(f"The language model call failed ({type(error).__name__}): {str(error)[:160]}") from error
        text = getattr(result, "text", "") or ""
        if not text.strip():
            raise MinutesError(f"The language model ({getattr(result, 'model', None) or 'unknown'}) returned no text; a reasoning model can spend its whole budget thinking. Try again.")
        return text, str(getattr(result, "model", "") or "")

    def _ask(self, link, system: str, user: str, effort: str) -> tuple[dict, str]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text, model = self._chat(link, messages, effort)
        data = parse_json_object(text)
        if data is None:  # one retry with the instruction repeated: small models forget "JSON only"
            messages.append({"role": "assistant", "content": text[:2000]})
            messages.append({"role": "user", "content": "That was not a single JSON object. Answer again with only the JSON object."})
            text, model = self._chat(link, messages, effort)
            data = parse_json_object(text)
        if data is None:
            raise MinutesError("The language model did not answer with JSON, twice. Try again or use another model.")
        return data, model

    def _header(self, session: dict) -> tuple[str, date]:
        started = datetime.fromtimestamp(session["started_at"])
        header = f"Meeting date: {started.date().isoformat()} ({WEEKDAYS_ES[started.weekday()]}). Title: {session['title']}."
        return header, started.date()

    def _write(self, session: dict, transcript: Transcript, link) -> dict:
        header, meeting_day = self._header(session)
        dropped = 0
        models: list[str] = []
        if transcript.chars <= SINGLE_PASS_CHARS:
            data, model = self._ask(link, SYSTEM_PROMPT, f"{header}\nReturn JSON shaped like {SCHEMA_HINT}. Summary: 5 to 10 lines.\n\nTRANSCRIPT:\n" + "\n".join(transcript.lines), "medium")
            models.append(model)
            items, dropped = validate_action_items(data.get("action_items"), transcript, meeting_day)
            summary, decisions = _summary_lines(data.get("summary")), _str_list(data.get("decisions"))
            questions, participants = _str_list(data.get("open_questions")), _str_list(data.get("participants"))
            chunks = 1
        else:
            pieces = transcript.chunks()
            chunks = len(pieces)
            partials: list[dict] = []
            groups: list[list[dict]] = []
            for number, lines in enumerate(pieces, start=1):
                first, last = lines[0][:8], lines[-1][:8]
                data, model = self._ask(
                    link, SYSTEM_PROMPT,
                    f"{header}\nThis is part {number} of {chunks} (from {first.strip('[] ')} to {last.strip('[] ')}). Return JSON shaped like {SCHEMA_HINT}. "
                    "Summary: 2 to 4 lines about this part only.\n\nTRANSCRIPT:\n" + "\n".join(lines), "low")
                models.append(model)
                part_items, part_dropped = validate_action_items(data.get("action_items"), transcript, meeting_day)
                dropped += part_dropped
                groups.append(part_items)
                partials.append({"part": number, "summary": _summary_lines(data.get("summary")), "decisions": _str_list(data.get("decisions")),
                                 "open_questions": _str_list(data.get("open_questions")), "participants": _str_list(data.get("participants"))})
            items = merge_action_items(groups)
            participants = _str_list([p for part in partials for p in part["participants"]])
            try:
                merged, model = self._ask(link, REDUCE_PROMPT, json.dumps(partials, ensure_ascii=False), "medium")
                models.append(model)
                summary, decisions = _summary_lines(merged.get("summary")), _str_list(merged.get("decisions"))
                questions = _str_list(merged.get("open_questions"))
            except MinutesError:  # keep the parts rather than lose a long meeting: their text is the model's own
                summary = _summary_lines([line for part in partials for line in part["summary"]])
                decisions = _str_list([d for part in partials for d in part["decisions"]])
                questions = _str_list([q for part in partials for q in part["open_questions"]])
        participants = [name for name in participants if transcript.contains_name(name)]
        items = merge_action_items([items])
        model_name = next((m for m in reversed(models) if m), "")
        return {
            "session_id": session["id"],
            "summary": "\n".join(summary),
            "decisions": decisions,
            "action_items": items,
            "open_questions": questions,
            "participants": participants,
            "model": model_name,
            "created_at": self.now(),
            "stats": {"chunks": chunks, "items_dropped_without_evidence": dropped, "transcript_chars": transcript.chars},
        }


class _NoModel(RuntimeError):
    """No language model resolved; surfaces as status `no_model`."""


# ------------------------------------------------------------------- rendering --
def render_minutes_md(session: dict, minutes: dict, heading: int = 1) -> str:
    """Markdown of the minutes alone (the export puts them above the transcript)."""
    h = "#" * heading
    sub = "#" * (heading + 1)
    stamp = datetime.fromtimestamp(session["started_at"]).strftime("%Y-%m-%d %H:%M") if session.get("started_at") else ""
    lines = [f"{h} Acta: {session.get('title') or 'Sesión sin título'}", ""]
    if stamp:
        lines += [f"- Fecha: {stamp}"]
    if minutes.get("participants"):
        lines += ["- Participantes: " + ", ".join(minutes["participants"])]
    if minutes.get("model"):
        lines += [f"- Generada con: {minutes['model']} (revisa las citas antes de fiarte)"]
    lines += ["", f"{sub} Resumen", ""]
    lines += [f"- {line}" for line in minutes["summary"].split("\n") if line.strip()] or ["_Sin resumen._"]
    if minutes.get("decisions"):
        lines += ["", f"{sub} Decisiones", ""] + [f"- {d}" for d in minutes["decisions"]]
    lines += ["", f"{sub} Acciones", ""]
    if not minutes.get("action_items"):
        lines.append("_Sin acciones detectadas._")
    for item in minutes.get("action_items", []):
        who = item["owner"] or "¿quién?"
        to = f" → {item['counterpart']}" if item.get("counterpart") else ""
        due = f" · para {item['due_date']}" if item.get("due_date") else (f" · «{item['due_text']}»" if item.get("due_text") else "")
        ev = item["evidence"]
        lines.append(f"- [ ] **{who}**{to}: {item['action']}{due}  \n  `{hms(ev['start_s'])}` {ev['speaker']}: «{ev['quote']}»")
    if minutes.get("open_questions"):
        lines += ["", f"{sub} Preguntas abiertas", ""] + [f"- {q}" for q in minutes["open_questions"]]
    return "\n".join(lines).rstrip() + "\n"
