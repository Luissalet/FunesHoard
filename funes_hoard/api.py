"""FastAPI application factory: `create_app(data_dir, static_dir) -> FastAPI`.

Also home to the browser-attack guard middleware, the UI-facing REST API,
and the `/api/agent/*` surface that the MCP adapter is a thin wrapper over.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from funes_hoard import __version__, queries
from funes_hoard.collector import Collector
from funes_hoard.core.classify import CATEGORIES, ClassifyRule, classify, default_rules
from funes_hoard.core.privacy import PauseState
from funes_hoard.db import Database
from funes_hoard.git_watch import GitCommitsPoller
from funes_hoard.jobs import JobManager
from funes_hoard.recent_files import RecentFilesPoller, windows_recent_dir
from funes_hoard.retention import delete_range, run_retention
from funes_hoard.scheduler import BackgroundScheduler
from funes_hoard.timeparse import TimeParseError

SERVICE = "funes-hoard"
DISPLAY_NAME = "Funes's Hoard"


def _setup_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("funes_hoard")
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)


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


class TimelineArgs(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    min_minutes: float = Field(default=2, ge=0)
    limit: int = Field(default=40, ge=1, le=100)


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


def create_app(data_dir: Path, static_dir: Optional[Path] = None, demo: bool = False, port: Optional[int] = None) -> FastAPI:
    data_dir = Path(data_dir)
    _setup_logging(data_dir)
    db = Database(data_dir)

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

    app = FastAPI(title=DISPLAY_NAME, lifespan=lifespan)
    app.state.db = db
    app.state.collector = collector
    app.state.jobs = jobs
    app.state.scheduler = scheduler
    app.state.port = port
    app.state.demo = demo

    # ------------------------------------------------------------ guard --
    LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

    @app.middleware("http")
    async def browser_attack_guard(request: Request, call_next):
        host_header = request.headers.get("host", "")
        hostname = host_header.rsplit(":", 1)[0] if ":" in host_header and not host_header.startswith("[") else host_header
        if host_header.startswith("["):
            hostname = host_header.split("]")[0] + "]"
        if hostname not in LOOPBACK_HOSTS:
            return JSONResponse({"error": "forbidden_host", "message": "Host header does not match a loopback address."}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            sec_fetch_site = request.headers.get("sec-fetch-site")
            if sec_fetch_site == "cross-site":
                return JSONResponse({"error": "forbidden_origin", "message": "Cross-site request rejected."}, status_code=403)
            if origin:
                own_origin_hosts = LOOPBACK_HOSTS
                m = re.match(r"^https?://([^/]+)$", origin)
                origin_host = m.group(1) if m else ""
                origin_hostname = origin_host.rsplit(":", 1)[0]
                if origin_hostname not in own_origin_hosts:
                    return JSONResponse({"error": "forbidden_origin", "message": "Origin does not match this app."}, status_code=403)
        response = await call_next(request)
        return response

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
            result = fn()
        except TimeParseError as exc:
            duration = (time.perf_counter() - started) * 1000
            _log_agent_call(tool, args_summary, duration, False, str(exc))
            raise HTTPException(status_code=400, detail={"error": "bad_time", "message": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            duration = (time.perf_counter() - started) * 1000
            _log_agent_call(tool, args_summary, duration, False, str(exc))
            raise HTTPException(status_code=500, detail={"error": "internal_error", "message": str(exc)})
        duration = (time.perf_counter() - started) * 1000
        _log_agent_call(tool, args_summary, duration, True, None)
        return result

    # ------------------------------------------------------ agent routes --
    @app.post("/api/agent/activity_now")
    def agent_activity_now():
        return run_agent("activity_now", "{}", lambda: queries.activity_now(db, collector))

    @app.post("/api/agent/activity_where_was_i")
    def agent_where_was_i(args: WhereWasIArgs):
        return run_agent(
            "activity_where_was_i", f"before={args.before} contexts={args.contexts}",
            lambda: queries.activity_where_was_i(db, args.before, args.contexts),
        )

    @app.post("/api/agent/activity_timeline")
    def agent_timeline(args: TimelineArgs):
        return run_agent(
            "activity_timeline", f"start={args.start} end={args.end} limit={args.limit}",
            lambda: queries.activity_timeline(db, args.start, args.end, args.min_minutes, args.limit),
        )

    @app.post("/api/agent/activity_summary")
    def agent_summary(args: SummaryArgs):
        return run_agent(
            "activity_summary", f"day={args.day} start={args.start} end={args.end}",
            lambda: queries.activity_summary(db, args.day, args.start, args.end, args.group_by),
        )

    @app.post("/api/agent/activity_search")
    def agent_search(args: SearchArgs):
        return run_agent(
            "activity_search", f"query={args.query!r} limit={args.limit}",
            lambda: queries.activity_search(db, args.query, args.since, args.until, args.limit),
        )

    @app.post("/api/agent/activity_recent_files")
    def agent_recent_files(args: RecentFilesArgs):
        return run_agent(
            "activity_recent_files", f"since={args.since} limit={args.limit}",
            lambda: queries.activity_recent_files(db, args.since, args.limit),
        )

    @app.post("/api/agent/activity_projects")
    def agent_projects(args: ProjectsArgs):
        return run_agent(
            "activity_projects", f"since={args.since} limit={args.limit}",
            lambda: queries.activity_projects(db, args.since, args.limit),
        )

    @app.post("/api/agent/activity_pause")
    def agent_pause(args: PauseArgs):
        def _do():
            until = collector.pause(args.minutes)
            return {"paused": True, "until": until, "minutes": args.minutes}

        return run_agent("activity_pause", f"minutes={args.minutes}", _do)

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
    def ui_timeline(start: Optional[str] = None, end: Optional[str] = None, min_minutes: float = 2, limit: int = 200):
        return queries.activity_timeline(db, start, end, min_minutes, limit)

    @app.get("/api/summary")
    def ui_summary(day: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None, group_by: str = "category"):
        return queries.activity_summary(db, day, start, end, group_by)

    @app.get("/api/search")
    def ui_search(query: str, since: Optional[str] = None, until: Optional[str] = None, limit: int = 30):
        return queries.activity_search(db, query, since, until, limit)

    @app.get("/api/recent-files")
    def ui_recent_files(since: Optional[str] = None, limit: int = 50):
        return queries.activity_recent_files(db, since, limit)

    @app.get("/api/projects")
    def ui_projects(since: Optional[str] = None, limit: int = 30):
        return queries.activity_projects(db, since, limit)

    @app.get("/api/where-was-i")
    def ui_where_was_i(before: Optional[str] = None, contexts: int = 5):
        return queries.activity_where_was_i(db, before, contexts)

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
            days.append({"date": day_str, **queries.activity_summary(db, day_str, None, None, "category")})
        return {"days": days}

    # ----------------------------------------------------- privacy admin --
    @app.get("/api/privacy/rules")
    def privacy_rules_get():
        rows = db.query("SELECT * FROM privacy_rules ORDER BY id ASC")
        return {"items": [dict(r) for r in rows]}

    @app.post("/api/privacy/rules")
    def privacy_rules_add(rule: PrivacyRuleIn):
        if rule.kind not in ("exclude", "redact"):
            raise HTTPException(400, {"error": "bad_kind", "message": "kind must be exclude or redact"})
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
    @app.get("/api/classify/rules")
    def classify_rules_get():
        rows = db.query("SELECT * FROM classify_rules ORDER BY order_idx ASC")
        return {"items": [dict(r) for r in rows], "categories": CATEGORIES}

    @app.post("/api/classify/rules")
    def classify_rules_add(rule: ClassifyRuleIn):
        max_order = db.query_one("SELECT COALESCE(MAX(order_idx), -1) m FROM classify_rules")["m"]
        rid = db.execute(
            "INSERT INTO classify_rules(order_idx, match_type, pattern, category, project, enabled) VALUES (?, ?, ?, ?, ?, ?)",
            (max_order + 1, rule.match_type, rule.pattern, rule.category, rule.project, int(rule.enabled)),
        )
        return {"id": rid}

    @app.put("/api/classify/rules/{rule_id}")
    def classify_rules_update(rule_id: int, rule: ClassifyRuleIn):
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
            repo_rows = db.query("SELECT path FROM commit_repos WHERE enabled = 1")
            repos = [Path(r["path"]).name for r in repo_rows]
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
        rid = db.execute(
            "INSERT OR IGNORE INTO commit_repos(path, enabled) VALUES (?, ?)", (body.path, int(body.enabled))
        )
        return {"id": rid}

    @app.delete("/api/commit-repos/{repo_id}")
    def commit_repos_delete(repo_id: int):
        db.execute("DELETE FROM commit_repos WHERE id = ?", (repo_id,))
        return {"deleted": repo_id}

    # --------------------------------------------------------------- SPA --
    if static_dir is not None and Path(static_dir).is_dir():
        app.mount("/assets", StaticFiles(directory=str(Path(static_dir) / "assets")), name="assets")
        index_file = Path(static_dir) / "index.html"

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404)
            requested = Path(static_dir) / full_path
            if full_path and requested.is_file():
                return FileResponse(requested)
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
