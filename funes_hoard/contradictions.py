"""Spot memories that contradict each other about the same subject.

Deterministic, no model: `recall` / `recall_search` hand their merged items to
`resolve()`, which extracts small normalised claims from each item's text and
compares the claims of different items.

Three kinds of claim, in Spanish and English:

* attribute: "<subject> is/es/:/moves to <typed value>" where the value is a
  time, weekday, date, amount (with unit) or version. The same subject with
  disjoint values of the same type is a conflict ("the meeting is on Tuesday"
  vs "the meeting is on Thursday"). Untyped values ("Ana is an engineer") are
  never compared, so compatible facts are not flagged.
* negation: the same words once affirmed and once negated ("we are going to
  Lisbon" / "we are not going to Lisbon", "ya no vamos a Lisboa").
* opposite: the same words with opposite state words ("server active" /
  "server inactive", "confirmed" / "cancelled").

Resolution per conflicting subject: the claim backed by more distinct items
wins; on a tie the most recent item wins. The losers are NOT deleted: they
leave `items` (unless the caller asks to include them) and are reported in
`contradictions: [{kept, dropped, reason}]`; dropped items that are included
carry `status: "descartado por contradicci\u00f3n"` and a link (`kept_citation`)
to the item that was kept.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

DROPPED_STATUS = "descartado por contradicci\u00f3n"
SKIP_KINDS = {"episode"}  # a foreground window title is not a claim
MIN_TEXT = 8

_STOP = set("""
el la los las un una unos unas de del al a en y e o u que se lo le les me mi mis tu tus su sus con por para
es son esta estan estaba estaban sera seran fue fueron ser estar hay ha han he hemos va ya muy mas
the an of to in on at and or is are was were be been being it its this that these those my your our their
do does did has have had will would can could should shall just now ahora hoy today actualmente currently
for with from by as
""".split())
_NEGATORS = {"no", "not", "never", "nunca", "jamas", "tampoco", "nor"}
_VERBS = (
    r"es|son|sera|seran|esta|estan|is|are|will be|ahora es|now is|pasa a|pasa al|se mueve a|se mueve al|"
    r"se cambia a|cambia a|cambia al|se pospone a|se pospone al|se retrasa a|queda para|queda en|"
    r"moves to|moved to|changed to|rescheduled to|postponed to|moved|rescheduled|postponed"
)
_ATTR_RE = re.compile(r"^(?P<subj>.+?)\s*(?::|=|\b(?:" + _VERBS + r")\b)\s*(?P<val>.+)$")

_WEEKDAYS = {
    "lunes": "mon", "martes": "tue", "miercoles": "wed", "jueves": "thu", "viernes": "fri", "sabado": "sat",
    "domingo": "sun", "monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
    "friday": "fri", "saturday": "sat", "sunday": "sun",
}
_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_UNITS = {
    "eur": "eur", "euro": "eur", "euros": "eur", "\u20ac": "eur", "usd": "usd", "$": "usd", "dolares": "usd",
    "dollars": "usd", "dollar": "usd", "%": "%", "km": "km", "kg": "kg", "gb": "gb", "mb": "mb",
    "minutos": "min", "minutes": "min", "min": "min", "horas": "h", "hours": "h", "dias": "d", "days": "d",
}
_OPPOSITES = [  # (pair id, pole A words, pole B words)
    ("active", {"activo", "activa", "active", "enabled", "habilitado", "habilitada", "encendido", "on", "online"},
     {"inactivo", "inactiva", "inactive", "disabled", "deshabilitado", "deshabilitada", "apagado", "off", "offline"}),
    ("open", {"abierto", "abierta", "open", "opened"}, {"cerrado", "cerrada", "closed"}),
    ("approved", {"aprobado", "aprobada", "approved", "aceptado", "aceptada", "accepted"},
     {"rechazado", "rechazada", "rejected", "denegado", "denied"}),
    ("confirmed", {"confirmado", "confirmada", "confirmed"}, {"cancelado", "cancelada", "cancelled", "canceled"}),
    ("paid", {"pagado", "pagada", "paid"}, {"impagado", "impagada", "unpaid"}),
    ("available", {"disponible", "available"}, {"unavailable", "ocupado", "ocupada"}),
    ("result", {"exitoso", "exitosa", "passed", "successful"}, {"fallido", "fallida", "failed", "failing"}),
]
_OPP_INDEX = {w: (pid, 0) for pid, a, _b in _OPPOSITES for w in a}
_OPP_INDEX.update({w: (pid, 1) for pid, _a, b in _OPPOSITES for w in b})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\b(can)'?not\b", r"\1 not", text)
    text = re.sub(r"\bwon't\b", "will not", text)
    text = re.sub(r"(\w)n't\b", r"\1 not", text)
    return text


def _stem(tok: str) -> str:
    return tok[:8]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text)


def _sentences(text: str) -> list[str]:
    out = []
    for part in re.split(r"(?<=[.;!?])\s+|\n", text):
        part = part.strip(" .;!\t")
        if len(part) >= MIN_TEXT and "?" not in part and not re.match(r"^(si|if|when|cuando)\b", part):
            out.append(part)
    return out


def _claim_text(item: dict) -> str:
    text = str(item.get("text") or "")
    if item.get("source") == "scribe":
        text = re.sub(r"^\S{1,24}:\s+", "", text)  # "SPEAKER_1: ..." -> the words
    elif item.get("source") == "argus" and " \u2014 " in text:
        text = text.split(" \u2014 ", 1)[1]  # "window title - OCR excerpt"
    return text

# ----------------------------------------------------------- typed values --
def _typed_values(val: str) -> dict[str, set[str]]:
    """Time, weekday, date, amount and version values found in `val`, by type."""
    found: dict[str, set[str]] = {}
    work = val

    def take(pattern: str, fn) -> None:
        nonlocal work

        def sub(m):
            fn(m)
            return " " * (m.end() - m.start())

        work = re.sub(pattern, sub, work)

    def add(kind: str, v: str) -> None:
        found.setdefault(kind, set()).add(v)

    month_alt = "|".join(sorted(_MONTHS, key=len, reverse=True))
    take(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: add("date", f"{int(m[2]):02d}-{int(m[3]):02d}"))
    take(r"\b(\d{1,2})\s*(?:de\s+)?(" + month_alt + r")\b", lambda m: add("date", f"{_MONTHS[m[2]]:02d}-{int(m[1]):02d}"))
    take(r"\b(" + month_alt + r")\s+(\d{1,2})\b", lambda m: add("date", f"{_MONTHS[m[1]]:02d}-{int(m[2]):02d}"))
    take(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b", lambda m: add("date", f"{int(m[2]):02d}-{int(m[1]):02d}"))

    def clock(m) -> None:
        hour, minute = int(m[1]), int(m[2] or 0)
        ampm = m[3]
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour <= 23 and minute <= 59:
            add("time", f"{hour:02d}:{minute:02d}")

    take(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", clock)
    take(r"\b(\d{1,2})h(\d{2})?\b", lambda m: add("time", f"{int(m[1]):02d}:{int(m[2] or 0):02d}"))
    take(r"\b(\d{1,2})()\s*(am|pm)\b", clock)
    take(r"\b(?:a las|a la|at|sobre las)\s+(\d{1,2})()()\b(?!\s*[%\w])", clock)
    for m in re.finditer(r"\b(" + "|".join(_WEEKDAYS) + r")\b", work):
        add("weekday", _WEEKDAYS[m[1]])
    take(r"\bv?(\d+\.\d+\.\d+)\b", lambda m: add("version", m[1]))
    unit_alt = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(" + unit_alt + r")?(?![\w])", work):
        num = float(m[1].replace(",", "."))
        unit = _UNITS.get(m[2] or "", "")
        add("num:" + unit, f"{num:g}")
    return found


# ----------------------------------------------------------------- claims --
def _item_claims(item: dict) -> dict[tuple, set[str]]:
    """key -> values, for one item. Keys never repeat inside an item with two
    different value sets (an item that contradicts itself is left alone)."""
    claims: dict[tuple, set[str]] = {}
    clashing: set[tuple] = set()

    def put(key: tuple, values: set[str]) -> None:
        if key in claims and claims[key] != values:
            clashing.add(key)
        claims.setdefault(key, set()).update(values)

    for raw in _sentences(_claim_text(item)):
        s = _fold(raw)
        toks = _tokens(s)
        neg = [t for t in toks if t in _NEGATORS]
        if len(neg) > 1:
            continue
        negated = bool(neg)
        if not negated:
            m = _ATTR_RE.match(s)
            if m:
                subj = [_stem(t) for t in _tokens(m["subj"]) if t not in _STOP]
                if 1 <= len(subj) <= 6:
                    for vtype, values in _typed_values(m["val"]).items():
                        put(("attr", tuple(subj), vtype), values)
        opp = [t for t in toks if t in _OPP_INDEX]
        rest = sorted({_stem(t) for t in toks if t not in _STOP and t not in _NEGATORS and t not in _OPP_INDEX})
        if len(opp) == 1 and not negated and rest:
            pid, pole = _OPP_INDEX[opp[0]]
            put(("opp", pid, tuple(rest)), {"AB"[pole]})
        content = sorted({_stem(t) for t in toks if t not in _STOP and t not in _NEGATORS})
        if len(content) >= 2 and not opp:
            put(("neg", tuple(content)), {"neg" if negated else "pos"})
    for key in clashing:
        claims.pop(key, None)
    return claims


def _ts(item: dict) -> float:
    try:
        return datetime.fromisoformat(str(item.get("time"))).timestamp()
    except (TypeError, ValueError):
        return float("-inf")


def _label(key: tuple, a: set[str], b: set[str]) -> tuple[str, str]:
    av, bv = "/".join(sorted(a)), "/".join(sorted(b))
    if key[0] == "attr":
        subj = " ".join(key[1])
        return (f"valor distinto para \u00ab{subj}\u00bb ({key[2].split(':')[0]}): {av} frente a {bv}",
                f"different value for \"{subj}\" ({key[2].split(':')[0]}): {av} vs {bv}")
    if key[0] == "neg":
        return ("uno afirma y el otro niega lo mismo", "one states and the other denies the same thing")
    return (f"estados opuestos de lo mismo ({key[1]})", f"opposite states of the same thing ({key[1]})")


def _ref(item: dict) -> dict:
    return {k: item.get(k) for k in ("citation", "time", "source", "text")}


def resolve(items: list[dict], include_dropped: bool = False) -> tuple[list[dict], list[dict]]:
    """-> (items to show, contradictions). Never mutates `items`; dropped items
    are copied with `status`, `discard_reason` and `kept_citation`."""
    by_key: dict[tuple, list[tuple[int, set[str]]]] = {}
    for idx, item in enumerate(items):
        if item.get("kind") in SKIP_KINDS or not item.get("text"):
            continue
        for key, values in _item_claims(item).items():
            by_key.setdefault(key, []).append((idx, values))

    verdict: dict[int, dict] = {}  # dropped idx -> {kept idx, reasons}
    for key, entries in sorted(by_key.items(), key=lambda kv: str(kv[0])):
        if len(entries) < 2:
            continue
        support = {i: sum(1 for _j, w in entries if w & v) for i, v in entries}
        win_idx, win_vals = max(entries, key=lambda e: (support[e[0]], _ts(items[e[0]]), -e[0]))
        for idx, vals in entries:
            if idx != win_idx and not (vals & win_vals) and idx not in verdict:
                es, en = _label(key, vals, win_vals)
                verdict[idx] = {"kept": win_idx, "rule": key[0], "es": es, "en": en,
                                "support": (support[win_idx], support[idx])}

    for idx in list(verdict):  # a kept item that was itself dropped: follow it
        seen = {idx}
        while verdict[idx]["kept"] in verdict and verdict[idx]["kept"] not in seen:
            seen.add(verdict[idx]["kept"])
            verdict[idx]["kept"] = verdict[verdict[idx]["kept"]]["kept"]
    verdict = {i: v for i, v in verdict.items() if v["kept"] not in verdict or v["kept"] == i}

    contradictions, shown = [], []
    for idx, item in enumerate(items):
        v = verdict.get(idx)
        if v is None:
            shown.append(item)
            continue
        kept = items[v["kept"]]
        basis_es = "m\u00e1s respaldo" if v["support"][0] > v["support"][1] else "m\u00e1s reciente"
        basis_en = "more support" if v["support"][0] > v["support"][1] else "more recent"
        contradictions.append({
            "kept": _ref(kept), "dropped": _ref(item), "rule": v["rule"],
            "reason": f"{DROPPED_STATUS}: {v['es']}; se conserva {kept.get('citation')} ({basis_es})",
            "reason_en": f"discarded by contradiction: {v['en']}; kept {kept.get('citation')} ({basis_en})",
        })
        if include_dropped:
            shown.append({**item, "status": DROPPED_STATUS, "discard_reason": contradictions[-1]["reason"],
                          "kept_citation": kept.get("citation")})
    return shown, contradictions