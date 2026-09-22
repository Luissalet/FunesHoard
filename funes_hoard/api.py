"""FastAPI application factory: `create_app(data_dir, static_dir) -> FastAPI`.

Also home to the browser-attack guard middleware, the UI-facing REST API,
and the `/api/agent/*` surface that the MCP adapter is a thin wrapper over.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from funes_hoard import __version__, backend, queries
from funes_hoard.collector import Collector, _known_repo_names
from funes_hoard.core.classify import CATEGORIES, ClassifyRule, classify
from funes_hoard.db import Database
from funes_hoard.errors import BadInput
from funes_hoard.git_watch import GitCommitsPoller, configured_authors
from funes_hoard.hoard_link import BackendError, Link, Unavailable
from funes_hoard.jobs import JobManager
from funes_hoard.recent_files import RecentFilesPoller, windows_recent_dir
from funes_hoard.retention import delete_range, run_retention
from funes_hoard.scheduler import BackgroundScheduler
from funes_hoard.timeparse import parse_day_or_range

SERVICE = "funes-hoard"
PID_FILE = "funes.pid"
DISPLAY_NAME = "Funes's Hoard"


def _setup_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = None
    # uvicorn's own errors (e.g. "address already in use") go to the same
    # file: when started hidden by start.ps1 there is no console to read.
    for name, level in (("funes_hoard", logging.INFO), ("uvicorn.error", logging.WARNING)):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
            continue
        if handler is None:
            # delay=True: the file is only opened on the first record, so an
            # app created in tests does not keep a handle open (Windows
            # cannot delete a temp dir holding an open file).
            handler = logging.handlers.RotatingFileHandler(
                log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)


def _remove_own_pid_file(data_dir: Path) -> None:
    pid_file = data_dir / PID_FILE
    try:
        if json.loads(pid_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
            pid_file.unlink()
    except (OSError, ValueError):
        pass


def _pick_probe(demo: bool):
    if demo:
        from funes_hoard.demo import LiveDemoProbe

        return LiveDemoProbe()
    if sys.platform == "win32":
        from funes_hoard.collectors.windows import WindowsProbe

        return WindowsProbe()
    from funes_hoard.collectors.linux import LinuxProbe

    return LinuxProbe()


# ---------------------------------------------------------------- models --
class PauseArgs(BaseModel):
    minutes: float = Field(default=15, ge=1, le=24 * 60)


class WhereWasIArgs(BaseModel):
    before: Optional[str] = None
    contexts: int = Field(default=5, ge=1, le=20)
    all_categories: bool = False


class TimelineArgs(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    # A6: None means "pick a sensible granularity for the range" (2 min for
    # a day or less, 5 min beyond that) rather than always 2.
    min_minutes: Optional[float] = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SummaryArgs(BaseModel):
    day: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    group_by: str = "category"


class SearchArgs(BaseModel):
    query: str
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class RecentFilesArgs(BaseModel):
    since: Optional[str] = None
    limit: int = Field(default=15, ge=1, le=100)


class ProjectsArgs(BaseModel):
    since: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class PrivacyRuleIn(BaseModel):
    kind: str
    match_type: str
    pattern: str
    enabled: bool = True


class ClassifyRuleIn(BaseModel):
    match_type: str
    pattern: str
    category: str
    project: Optional[str] = None
    enabled: bool = True


class ReorderIn(BaseModel):
    order: list[int]


class RetentionIn(BaseModel):
    days: int = Field(ge=1, le=3650)


class DeleteRangeIn(BaseModel):
    start: float
    end: float


class CommitRepoIn(BaseModel):
    path: str
    enabled: bool = True


class CommitAuthorsIn(BaseModel):
    authors: list[str] = Field(default_factory=list, max_length=20)


class BackendCapabilityIn(BaseModel):
    url: Optional[str] = None
    model: Optional[str] = None


class BackendConfigIn(BaseModel):
    only_resident: Optional[bool] = None
    faustus_url: Optional[str] = None
    faustus_token: Optional[str] = None
    capabilities: dict[str, BackendCapabilityIn] = Field(default_factory=dict)


class DayNarrativeIn(BaseModel):
    day: Optional[str] = None
    force: bool = False


class FeatureToggleIn(BaseModel):
    enabled: bool


class MeetingsAwaySettingIn(BaseModel):
    minutes: float = Field(ge=5, le=180)


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
AGENT_TOOLS = (
    "activity_now", "activity_where_was_i", "activity_timeline", "activity_summary",
    "activity_search", "activity_recent_files", "activity_projects", "activity_pause",
)
PRIVACY_MATCH_TYPES = ("app", "title_regex")
CLASSIFY_MATCH_TYPES = ("app", "title_regex", "domain")


def _split_host(value: str) -> tuple[str, Optional[int], bool]:
    """'127.0.0.1:8813' -> ('127.0.0.1', 8813, True); malformed -> ok=False."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return "", None, False
        host, rest = value[: end + 1], value[end + 1:]
    else:
        host, sep, port = value.partition(":")
        rest = f":{port}" if sep else ""
    if not rest:
        return host, None, True
    if not rest.startswith(":") or not rest[1:].isdigit():
        return "", None, False
    return host, int(rest[1:]), True


def _host_allowed(host: str, port: Optional[int], app_port: Optional[int]) -> bool:
    if host not in LOOPBACK_HOSTS:
        return False
    if app_port is None:
        return True
    return port == app_port or (port is None and app_port == 80)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def _validate_rule(match_type: str, pattern: str, allowed: tuple[str, ...]) -> None:
    if match_type not in allowed:
        raise BadInput("bad_match_type", f"match_type must be one of {', '.join(allowed)} (got {match_type!r}).")
    if not pattern.strip():
        raise BadInput("bad_pattern", "pattern must not be empty.")
    if match_type == "title_regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            raise BadInput("bad_pattern", f"invalid regular expression: {exc}") from exc


def create_app(
    data_dir: Path,
    static_dir: Optional[Path] = None,
    demo: bool = False,
    port: Optional[int] = None,
    link=None,
    link_factory: Optional[Callable[[object], object]] = None,
) -> FastAPI:
    """`link`/`link_factory` let tests inject a fake Hoard Link (or one
    built on an `httpx.MockTransport`) so the shared-model-backend routes
    stay offline in the suite: see `tests/test_backend.py`."""
    data_dir = Path(data_dir)
    _setup_logging(data_dir)
    db = Database(data_dir)
    link_factory = link_factory or Link
    if link is None:
        link = link_factory(backend.load_link_config(data_dir))

    if demo:
        from funes_hoard.demo import seed_demo_data

        has_any = db.query_one("SELECT COUNT(*) c FROM spans")
        if not has_any or has_any["c"] == 0:
            seed_demo_data(db)

    probe = _pick_probe(demo)
    collector = Collector(db, probe, interval_s=float(db.get_meta("sample_interval_s", "1") or 1))

    recent_poller = RecentFilesPoller(db, windows_recent_dir() if sys.platform == "win32" else None)
    git_poller = GitCommitsPoller(db)
    jobs = JobManager()
    scheduler = BackgroundScheduler(db=db, collector=collector, recent_poller=recent_poller, git_poller=git_poller)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        collector.start()
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()
            collector.stop()
            await app.state.link.aclose()
            db.close()
            _remove_own_pid_file(data_dir)

    app = FastAPI(title=DISPLAY_NAME, lifespan=lifespan)
    app.state.db = db
    app.state.collector = collector
    app.state.jobs = jobs
    app.state.scheduler = scheduler
    app.state.port = port
    app.state.demo = demo
    app.state.link = link
    app.state.link_factory = link_factory

    # ------------------------------------------------------------ guard --
    @app.middleware("http")
    async def browser_attack_guard(request: Request, call_next):
        # DNS rebinding: a hostile page whose domain resolves to 127.0.0.1
        # still sends its own name (or the wrong port) in Host.
        host, host_port, ok = _split_host(request.headers.get("host", ""))
        if not ok or not _host_allowed(host, host_port, port):
            return _error(403, "forbidden_host", "Host header does not match this app's loopback address.")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("sec-fetch-site") == "cross-site":
                return _error(403, "forbidden_origin", "Cross-site request rejected.")
            origin = request.headers.get("origin")
            if origin is not None:
                parts = urlsplit(origin)
                try:
                    origin_port = parts.port
                except ValueError:
                    origin_port = -1
                if origin_port is None:
                    origin_port = 443 if parts.scheme == "https" else 80
                own = (
                    parts.scheme == "http"
                    and (parts.hostname or "") in LOOPBACK_HOSTS
                    and (port is None or origin_port == port)
                )
                if not own:
                    return _error(403, "forbidden_origin", "Origin does not match this app.")
        return await call_next(request)

    # ----------------------------------------------------------- errors --
    @app.exception_handler(BadInput)
    async def bad_input_handler(_: Request, exc: BadInput):
        return _error(400, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(exc.detail, status_code=exc.status_code)
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, f"http_{exc.status_code}")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        problems = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
            problems.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
        message = "; ".join(problems) or "invalid arguments"
        tool = request.url.path.rsplit("/", 1)[-1]
        if request.url.path.startswith("/api/agent/") and tool in AGENT_TOOLS:
            _log_agent_call(tool, "(invalid arguments)", 0.0, False, message)
        return _error(400, "bad_arguments", message)

    # ------------------------------------------------------------ health --
    @app.get("/api/health")
    def health():
        span_count = db.query_one("SELECT COUNT(*) c FROM spans")["c"]
        return {
            "service": SERVICE,
            "name": DISPLAY_NAME,
            "version": __version__,
            "status": "ok",
            "recording": not collector.is_paused(),
            "spans": span_count,
            "demo": demo,
        }

    # -------------------------------------------------------- agent log --
    def _log_agent_call(tool: str, args_summary: str, duration_ms: float, ok: bool, error: Optional[str]) -> None:
        db.execute(
            "INSERT INTO agent_calls(ts, tool, args_summary, duration_ms, ok, error) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), tool, args_summary, duration_ms, 1 if ok else 0, error),
        )

    def run_agent(tool: str, args_summary: str, fn: Callable[[], dict]):
        started = time.perf_counter()
        try:
            result = queries.agent_view(fn())
        except BadInput as exc:
            _log_agent_call(tool, args_summary, (time.perf_counter() - started) * 1000, False, exc.message)
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logging.getLogger("funes_hoard.api").exception("agent tool %s failed", tool)
            _log_agent_call(tool, args_summary, (time.perf_counter() - started) * 1000, False, type(exc).__name__)
            raise HTTPException(status_code=500, detail={
                "error": "internal_error",
                "message": f"{tool} failed unexpectedly ({type(exc).__name__}); details are in the app log.",
            })
        _log_agent_call(tool, args_summary, (time.perf_counter() - started) * 1000, True, None)
        return result

    def _args(**kwargs) -> str:
        """Compact, human-readable audit summary: only the arguments given."""
        return " ".join(f"{k}={v!r}" for k, v in kwargs.items() if v is not None) or "(defaults)"

    # ------------------------------------------------------ agent routes --
    @app.post("/api/agent/activity_now")
    def agent_activity_now():
        return run_agent("activity_now", "(no arguments)", lambda: queries.activity_now(db, collector))

    @app.post("/api/agent/activity_where_was_i")
    def agent_where_was_i(args: WhereWasIArgs):
        return run_agent(
            "activity_where_was_i", _args(before=args.before, contexts=args.contexts, all_categories=args.all_categories),
            lambda: queries.activity_where_was_i(db, args.before, args.contexts, all_categories=args.all_categories),
        )

    @app.post("/api/agent/activity_timeline")
    def agent_timeline(args: TimelineArgs):
        return run_agent(
            "activity_timeline",
            _args(start=args.start, end=args.end, min_minutes=args.min_minutes, limit=args.limit, offset=args.offset or None),
            lambda: queries.activity_timeline(db, args.start, args.end, args.min_minutes, args.limit, offset=args.offset),
        )

    @app.post("/api/agent/activity_summary")
    def agent_summary(args: SummaryArgs):
        return run_agent(
            "activity_summary", _args(day=args.day, start=args.start, end=args.end, group_by=args.group_by),
            lambda: queries.activity_summary(db, args.day, args.start, args.end, args.group_by),
        )

    @app.post("/api/agent/activity_search")
    def agent_search(args: SearchArgs):
        return run_agent(
            "activity_search", _args(query=args.query, since=args.since, until=args.until, limit=args.limit),
            lambda: queries.activity_search(db, args.query, args.since, args.until, args.limit),
        )

    @app.post("/api/agent/activity_recent_files")
    def agent_recent_files(args: RecentFilesArgs):
        return run_agent(
            "activity_recent_files", _args(since=args.since, limit=args.limit),
            lambda: queries.activity_recent_files(db, args.since, args.limit),
        )

    @app.post("/api/agent/activity_projects")
    def agent_projects(args: ProjectsArgs):
        return run_agent(
            "activity_projects", _args(since=args.since, limit=args.limit),
            lambda: queries.activity_projects(db, args.since, args.limit),
        )

    @app.post("/api/agent/activity_pause")
    def agent_pause(args: PauseArgs):
        def _do():
            until, extended = collector.pause_at_least(args.minutes)
            if until is None:
                note = "already paused until the user resumes it; nothing changed"
            elif extended:
                note = f"recording paused for {args.minutes:g} min; it resumes by itself"
            else:
                note = "already paused for longer than that; nothing changed"
            return {"paused": True, "until": until, "until_resumed": until is None, "note": note}

        return run_agent("activity_pause", _args(minutes=args.minutes), _do)

    # --------------------------------------------------------- UI routes --
    @app.get("/api/status")
    def status():
        info = queries.activity_now(db, collector)
        info["fts_available"] = getattr(db, "fts_available", False)
        info["rules_version"] = int(db.get_meta("rules_version", "1"))
        info["probe_status"] = probe.status()
        info["retention_days"] = int(float(db.get_meta("retention_days", "180") or 180))
        return info

    @app.get("/api/timeline")
    def ui_timeline(day: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                    min_minutes: float = 2, limit: int = 100, offset: int = 0):
        return queries.activity_timeline(db, start, end, min_minutes, limit, offset=offset, day=day)

    @app.get("/api/summary")
    def ui_summary(day: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None, group_by: str = "all"):
        return queries.activity_summary(db, day, start, end, group_by)

    @app.get("/api/search")
    def ui_search(query: str, since: Optional[str] = None, until: Optional[str] = None, limit: int = 30):
        return queries.activity_search(db, query, since, until, limit, marks=("\u0002", "\u0003"))

    @app.get("/api/commits")
    def ui_commits(since: Optional[str] = None, limit: int = 50):
        return queries.recent_commits(db, since, limit)

    @app.get("/api/recent-files")
    def ui_recent_files(since: Optional[str] = None, limit: int = 50):
        return queries.activity_recent_files(db, since, limit)

    @app.get("/api/projects")
    def ui_projects(since: Optional[str] = None, limit: int = 30):
        return queries.activity_projects(db, since, limit)

    @app.get("/api/where-was-i")
    def ui_where_was_i(before: Optional[str] = None, contexts: int = 5, all_categories: bool = False):
        return queries.activity_where_was_i(db, before, contexts, all_categories=all_categories)

    @app.get("/api/agent-calls")
    def ui_agent_calls(limit: int = 50):
        limit = max(1, min(limit, 200))
        rows = db.query("SELECT * FROM agent_calls ORDER BY id DESC LIMIT ?", (limit,))
        return {"items": [dict(r) for r in rows]}

    @app.get("/api/week")
    def ui_week(start: Optional[str] = None):
        now = time.time()
        if start:
            from funes_hoard.timeparse import parse_moment

            base = parse_moment(start, now)
        else:
            base = now
        from datetime import datetime, timedelta

        base_date = datetime.fromtimestamp(base).date()
        monday = base_date - timedelta(days=base_date.weekday())
        days = []
        for i in range(7):
            d = monday + timedelta(days=i)
            day_str = d.isoformat()
            days.append({"date": day_str, **queries.activity_summary(db, day_str, None, None, "all")})
        return {"days": days}

    # -------------------------------------------------- model backend --
    def _write_my_day_enabled() -> bool:
        return db.get_meta("write_my_day_enabled", "1") == "1"

    def _day_bounds_key(day: Optional[str]) -> tuple[str, float]:
        start_ts, _ = parse_day_or_range(day, None, None, time.time())
        return datetime.fromtimestamp(start_ts).date().isoformat(), start_ts

    @app.get("/api/backend")
    async def backend_status():
        cfg = app.state.link.config
        # cfg.faustus_urls always has a value (Hoard Link's built-in probe
        # default), so the *explicit* override shown in Settings comes from
        # the raw file instead -- otherwise saving any other field would
        # silently pin that default into backend.json as if the user had
        # typed it.
        raw = backend.read_backend_json(data_dir)
        raw_faustus = raw.get("faustus") or {}
        raw_llm = (raw.get("capabilities") or {}).get("llm") or {}
        return {
            "capabilities": await app.state.link.status(),
            "only_resident": cfg.only_resident,
            "faustus_url": raw_faustus.get("url"),
            "faustus_token_set": bool(cfg.faustus_token),
            "write_my_day_enabled": _write_my_day_enabled(),
            "llm_url_override": raw_llm.get("url"),
            "llm_model_override": raw_llm.get("model"),
        }

    @app.put("/api/backend/config")
    def backend_config_put(body: BackendConfigIn):
        patch = {
            "only_resident": body.only_resident,
            "faustus_url": body.faustus_url,
            "faustus_token": body.faustus_token,
            "capabilities": {k: v.model_dump() for k, v in body.capabilities.items()},
        }
        backend.apply_config_patch(data_dir, patch)
        # Explicit configuration is read fresh on every resolve() (never
        # cached), so swapping the config in place is enough -- no need to
        # recreate the Link or touch its httpx client/probe cache.
        app.state.link.config = backend.load_link_config(data_dir)
        return {"saved": True, "faustus_token_set": bool(app.state.link.config.faustus_token)}

    @app.post("/api/backend/recheck")
    async def backend_recheck():
        old = app.state.link
        app.state.link = app.state.link_factory(old.config)
        await old.aclose()
        return {"ok": True}

    @app.get("/api/day-narrative")
    async def day_narrative_get(day: Optional[str] = None):
        day_key, _ = _day_bounds_key(day)
        row = db.query_one("SELECT * FROM day_narratives WHERE day = ?", (day_key,))
        enabled = _write_my_day_enabled()
        resolution = await app.state.link.resolve("llm") if enabled else None
        return {
            "day": day_key,
            "text": row["text"] if row else None,
            "model": row["model"] if row else None,
            "generated_at": row["generated_at"] if row else None,
            "enabled": enabled,
            "available": bool(resolution and resolution.resolved),
            "reason": resolution.reason if resolution is not None else None,
        }

    @app.post("/api/day-narrative")
    async def day_narrative_generate(args: DayNarrativeIn):
        if not _write_my_day_enabled():
            raise BadInput("feature_disabled", '"Write my day" is turned off in Settings.')
        day_key, day_start_ts = _day_bounds_key(args.day)
        if not args.force:
            row = db.query_one("SELECT * FROM day_narratives WHERE day = ?", (day_key,))
            if row:
                return {"day": day_key, "text": row["text"], "model": row["model"], "generated_at": row["generated_at"], "cached": True}

        # Only the compact summary the activity_summary tool itself returns
        # (categories/apps/projects and totals, focus blocks) -- never raw
        # or redacted window titles.
        summary = queries.agent_view(queries.activity_summary(db, day_key, None, None, "all"))
        if not summary["active_s"]:
            # Nothing to narrate: asking the model anyway would only get an
            # invented day back, and caching it would make it look real.
            raise BadInput("no_activity", f"Nothing was recorded on {day_key}, so there is no day to write about.")
        try:
            result = await app.state.link.chat(
                backend.build_narrative_messages(summary), capability="llm",
                max_tokens=backend.NARRATIVE_MAX_TOKENS, temperature=0.4,
            )
        except Unavailable as exc:
            reasons = "; ".join(exc.reasons) if exc.reasons else "no reason recorded"
            raise BadInput(
                "llm_unavailable",
                f"No language model is available for \"Write my day\" ({reasons}). "
                "Faustus can serve one, or load one in Ollama.",
            ) from exc
        except BackendError as exc:
            raise BadInput("llm_error", f"The language model call failed: {exc}") from exc
        if not result.text.strip():
            raise BadInput(
                "llm_empty",
                f"The language model ({result.model or 'unknown model'}) returned no text -- a reasoning model can "
                "spend its whole budget thinking. Try again, or pick a non-reasoning model in Settings.",
            )

        generated_at = time.time()
        db.execute(
            "INSERT INTO day_narratives(day, day_start_ts, text, model, generated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(day) DO UPDATE SET text=excluded.text, model=excluded.model, generated_at=excluded.generated_at",
            (day_key, day_start_ts, result.text, result.model, generated_at),
        )
        return {"day": day_key, "text": result.text, "model": result.model, "generated_at": generated_at, "cached": False}

    @app.get("/api/settings/write-my-day")
    def write_my_day_setting_get():
        return {"enabled": _write_my_day_enabled()}

    @app.put("/api/settings/write-my-day")
    def write_my_day_setting_put(body: FeatureToggleIn):
        db.set_meta("write_my_day_enabled", "1" if body.enabled else "0")
        return {"enabled": body.enabled}

    @app.get("/api/settings/meetings-away")
    def meetings_away_setting_get():
        # A3: how long a Meetings/Media span may sit idle before it counts
        # as away -- much longer than the ordinary threshold, since a call
        # or a video with no keyboard/mouse input is not idleness.
        seconds = float(db.get_meta("away_after_meetings_s", "1800") or 1800)
        return {"minutes": seconds / 60.0}

    @app.put("/api/settings/meetings-away")
    def meetings_away_setting_put(body: MeetingsAwaySettingIn):
        db.set_meta("away_after_meetings_s", str(body.minutes * 60.0))
        return {"minutes": body.minutes}

    # ----------------------------------------------------- privacy admin --
    @app.get("/api/privacy/rules")
    def privacy_rules_get():
        rows = db.query("SELECT * FROM privacy_rules ORDER BY id ASC")
        return {"items": [dict(r) for r in rows]}

    @app.post("/api/privacy/rules")
    def privacy_rules_add(rule: PrivacyRuleIn):
        if rule.kind not in ("exclude", "redact"):
            raise BadInput("bad_kind", "kind must be exclude or redact")
        _validate_rule(rule.match_type, rule.pattern, PRIVACY_MATCH_TYPES)
        rid = db.execute(
            "INSERT INTO privacy_rules(kind, match_type, pattern, enabled) VALUES (?, ?, ?, ?)",
            (rule.kind, rule.match_type, rule.pattern, int(rule.enabled)),
        )
        return {"id": rid}

    @app.delete("/api/privacy/rules/{rule_id}")
    def privacy_rules_delete(rule_id: int):
        db.execute("DELETE FROM privacy_rules WHERE id = ?", (rule_id,))
        return {"deleted": rule_id}

    @app.post("/api/privacy/pause")
    def privacy_pause(args: PauseArgs):
        until = collector.pause(args.minutes)
        return {"paused": True, "until": until}

    @app.post("/api/privacy/pause-until-resumed")
    def privacy_pause_indefinite():
        collector.pause_indefinitely()
        return {"paused": True, "until": None}

    @app.post("/api/privacy/resume")
    def privacy_resume():
        db.set_meta("paused_until", "")
        return {"paused": False}

    @app.get("/api/privacy/retention")
    def privacy_retention_get():
        return {"days": int(float(db.get_meta("retention_days", "180") or 180))}

    @app.put("/api/privacy/retention")
    def privacy_retention_set(body: RetentionIn):
        db.set_meta("retention_days", str(body.days))
        return {"days": body.days}

    @app.post("/api/privacy/purge-now")
    def privacy_purge_now():
        job = jobs.submit("retention", lambda cb: run_retention(db))
        return {"job_id": job.id}

    @app.post("/api/privacy/delete-range")
    def privacy_delete_range(body: DeleteRangeIn):
        counts = delete_range(db, body.start, body.end)
        return {"deleted": counts}

    @app.get("/api/privacy/export")
    def privacy_export(start: Optional[float] = None, end: Optional[float] = None):
        start = start if start is not None else 0.0
        end = end if end is not None else time.time()
        spans = [dict(r) for r in db.query("SELECT * FROM spans WHERE start_ts >= ? AND start_ts < ? ORDER BY start_ts", (start, end))]
        files = [dict(r) for r in db.query("SELECT * FROM file_events WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))]
        commits = [dict(r) for r in db.query("SELECT * FROM commits WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))]
        payload = {"spans": spans, "file_events": files, "commits": commits, "exported_at": time.time()}
        return JSONResponse(payload, headers={"Content-Disposition": "attachment; filename=funes-export.json"})

    # --------------------------------------------------- classify admin --
    def _validate_classify(rule: ClassifyRuleIn) -> None:
        _validate_rule(rule.match_type, rule.pattern, CLASSIFY_MATCH_TYPES)
        if rule.category not in CATEGORIES:
            raise BadInput("bad_category", f"category must be one of {', '.join(CATEGORIES)}.")

    @app.get("/api/classify/rules")
    def classify_rules_get():
        rows = db.query("SELECT * FROM classify_rules ORDER BY order_idx ASC")
        return {"items": [dict(r) for r in rows], "categories": CATEGORIES}

    @app.post("/api/classify/rules")
    def classify_rules_add(rule: ClassifyRuleIn):
        _validate_classify(rule)
        max_order = db.query_one("SELECT COALESCE(MAX(order_idx), -1) m FROM classify_rules")["m"]
        rid = db.execute(
            "INSERT INTO classify_rules(order_idx, match_type, pattern, category, project, enabled) VALUES (?, ?, ?, ?, ?, ?)",
            (max_order + 1, rule.match_type, rule.pattern, rule.category, rule.project, int(rule.enabled)),
        )
        return {"id": rid}

    @app.put("/api/classify/rules/{rule_id}")
    def classify_rules_update(rule_id: int, rule: ClassifyRuleIn):
        _validate_classify(rule)
        db.execute(
            "UPDATE classify_rules SET match_type=?, pattern=?, category=?, project=?, enabled=? WHERE id=?",
            (rule.match_type, rule.pattern, rule.category, rule.project, int(rule.enabled), rule_id),
        )
        return {"updated": rule_id}

    @app.delete("/api/classify/rules/{rule_id}")
    def classify_rules_delete(rule_id: int):
        db.execute("DELETE FROM classify_rules WHERE id = ?", (rule_id,))
        return {"deleted": rule_id}

    @app.post("/api/classify/reorder")
    def classify_rules_reorder(body: ReorderIn):
        for idx, rid in enumerate(body.order):
            db.execute("UPDATE classify_rules SET order_idx = ? WHERE id = ?", (idx, rid))
        return {"ok": True}

    @app.post("/api/classify/preview")
    def classify_preview(rule: ClassifyRuleIn):
        _validate_classify(rule)
        candidate = ClassifyRule(id=-1, order_idx=-1, match_type=rule.match_type, pattern=rule.pattern, category=rule.category, project=rule.project, enabled=True)
        rows = db.query("SELECT id, app, exe, title, category, project FROM spans WHERE kind='active'")
        changed = 0
        for r in rows:
            cat, proj = classify(r["app"], r["exe"], r["title"], [candidate])
            if cat != "Other" and (cat != r["category"] or proj != r["project"]):
                changed += 1
        return {"would_change": changed, "sample_size": len(rows)}

    @app.post("/api/classify/reapply")
    def classify_reapply():
        def _do(progress):
            rows = db.query("SELECT * FROM spans WHERE kind='active'")
            rules = [
                ClassifyRule(id=r["id"], order_idx=r["order_idx"], match_type=r["match_type"], pattern=r["pattern"], category=r["category"], project=r["project"], enabled=bool(r["enabled"]))
                for r in db.query("SELECT * FROM classify_rules ORDER BY order_idx ASC")
            ]
            repos = _known_repo_names(db)
            new_version = int(db.get_meta("rules_version", "1")) + 1
            total = len(rows)
            for i, r in enumerate(rows):
                cat, proj = classify(r["app"], r["exe"], r["title"], rules, repos)
                db.execute(
                    "UPDATE spans SET category=?, project=?, rules_version=? WHERE id=?",
                    (cat, proj, new_version, r["id"]),
                )
                if i % 50 == 0:
                    progress(i, total)
            db.set_meta("rules_version", str(new_version))
            progress(total, total)
            return {"updated": total, "rules_version": new_version}

        job = jobs.submit("reclassify", _do)
        return {"job_id": job.id}

    # ------------------------------------------------------------- jobs --
    @app.get("/api/jobs")
    def jobs_list():
        return {"items": [job.__dict__ for job in jobs.list()]}

    @app.get("/api/jobs/{job_id}")
    def jobs_get(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, {"error": "not_found", "message": "no such job"})
        return job.__dict__

    # -------------------------------------------------------- commit repos --
    @app.get("/api/commit-repos")
    def commit_repos_get():
        return {"items": [dict(r) for r in db.query("SELECT * FROM commit_repos ORDER BY id ASC")]}

    @app.post("/api/commit-repos")
    def commit_repos_add(body: CommitRepoIn):
        # A2: a path that does not exist used to be accepted silently and
        # listed like a real one, with nothing to scan until the next poll.
        if not Path(body.path).is_dir():
            raise BadInput("bad_path", f"{body.path!r} is not a folder on this machine.")
        rid = db.execute(
            "INSERT OR IGNORE INTO commit_repos(path, enabled) VALUES (?, ?)", (body.path, int(body.enabled))
        )
        # Scan right away instead of waiting for the next 10-minute poll, so
        # "N repos found, last scanned just now" shows up immediately.
        jobs.submit("git_scan", lambda cb: {"commits_found": git_poller.poll_once()})
        return {"id": rid}

    @app.get("/api/commit-authors")
    def commit_authors_get():
        return {"authors": configured_authors(db)}

    @app.put("/api/commit-authors")
    def commit_authors_put(body: CommitAuthorsIn):
        cleaned = [a.strip().replace(",", " ") for a in body.authors if a.strip()]
        db.set_meta("commit_authors", ",".join(cleaned))
        return {"authors": cleaned}

    @app.delete("/api/commit-repos/{repo_id}")
    def commit_repos_delete(repo_id: int):
        db.execute("DELETE FROM commit_repos WHERE id = ?", (repo_id,))
        return {"deleted": repo_id}

    # --------------------------------------------------------------- SPA --
    if static_dir is not None and Path(static_dir).is_dir():
        root = Path(static_dir).resolve()
        if (root / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=str(root / "assets")), name="assets")
        index_file = root / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(404, {"error": "not_found", "message": f"No API route /{full_path}."})
            if full_path:
                # The decoded path can be absolute ("//etc/passwd",
                # "/C:/Windows/...") or climb out with "..": resolve it and
                # only serve files that are really inside the build folder.
                try:
                    candidate = (root / full_path).resolve()
                except (OSError, ValueError):
                    candidate = None
                if candidate is not None and candidate.is_relative_to(root) and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(index_file)
    else:
        @app.get("/")
        def no_ui():
            return Response(
                "<html><body><h1>Funes's Hoard</h1>"
                "<p>The frontend has not been built yet. Run "
                "<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code> "
                "and restart the app.</p></body></html>",
                media_type="text/html",
            )

    return app
