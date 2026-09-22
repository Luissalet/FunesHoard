#!/usr/bin/env python3
"""Walk the person use cases (docs/USE_CASES.md) in a real browser.

    python scripts/uxtest_data.py serve --port 18830 &
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python scripts/ui_walkthrough.py --port 18830

Screenshots go to data-uxtest/shots/ (gitignored) and are meant to be
looked at, not just produced: each step prints what it did, how long the
screen took to settle, what it checked, and any console error. Needs
`playwright` in the interpreter that runs it (a dev tool, not a runtime
dependency). A failed check is printed as "CHECK FAILED" and makes the
script exit non-zero, so a regression in a use case cannot pass silently.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18830)
    ap.add_argument("--out", default=str(REPO_ROOT / "data-uxtest" / "shots"))
    ap.add_argument("--only", default="", help="comma-separated step prefixes to run")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = [s for s in a.only.split(",") if s]
    errors: list[str] = []
    failed: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(f"  {'check ok' if ok else 'CHECK FAILED'}: {what}")
        if not ok:
            failed.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch(timeout=600_000)  # a shared, loaded machine can be slow to start it

        def page_for(w: int, h: int, locale: str):
            ctx = browser.new_context(viewport={"width": w, "height": h}, locale=locale, accept_downloads=True)
            pg = ctx.new_page()
            pg.set_default_timeout(60_000)
            # A 400 the UI asked for on purpose (the bad-folder check in UC6)
            # is logged by the browser as a failed resource; everything else
            # is a real error.
            pg.on("console", lambda m: m.type == "error" and "status of 400" not in m.text and errors.append(f"{pg.url}: {m.text}"))
            pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e}"))
            return pg

        def settle(pg, what: str, fn) -> float:
            t0 = time.perf_counter()
            fn()
            pg.wait_for_load_state("networkidle")
            pg.wait_for_function("() => !document.querySelector('button[disabled].btn-primary')", timeout=30000)
            pg.wait_for_timeout(350)
            dt = time.perf_counter() - t0
            print(f"  {what}: {dt * 1000:.0f} ms")
            return dt

        def shot(pg, name: str, full: bool = False) -> None:
            pg.screenshot(path=str(out / f"{name}.png"), full_page=full)
            print(f"  -> {name}.png")

        def step(name: str) -> bool:
            run = not only or any(name.startswith(o) for o in only)
            if run:
                print(f"[{name}]")
            return run

        pg = page_for(1280, 800, "en-GB")

        if step("uc1"):
            settle(pg, "open app", lambda: pg.goto(base + "/"))
            shot(pg, "uc1-01-today-1280")
            card = pg.locator(".where-card")
            check(card.count() == 1, "Today has a 'Where was I?' card")
            first = card.locator(".where-item").first
            check(first.count() == 1, "the card lists at least one context")
            if first.count():
                text = first.inner_text()
                print(f"  first context: {text.splitlines()[0]!r}")
                check("Spotify" not in text and "WhatsApp" not in text, "the first context is work, not the music player or chat")
            card.scroll_into_view_if_needed()
            shot(pg, "uc1-02-where-card")
            settle(pg, "yesterday", lambda: pg.click("text=Yesterday"))
            check(pg.locator(".header h2").inner_text() == "Yesterday", "the header says Yesterday, not Today (C3)")
            check("#/today/" in pg.url, f"the day is in the address ({pg.url})")
            shot(pg, "uc1-03-yesterday")
            ycard = pg.locator(".where-card .where-item").first
            if ycard.count():
                settle(pg, "show in timeline", lambda: ycard.locator("button").click())
                check(pg.locator(".timeline-seg.selected").count() == 1, "'Show in timeline' pins a segment")
                detail = pg.locator(".timeline-detail").inner_text()
                print(f"  pinned: {detail.splitlines()[0]!r}")
                shot(pg, "uc1-04-pinned-from-where")
            check(pg.locator(".timeline-seg.seg-away").count() > 0, "away time is drawn on the timeline (C2)")
            check(pg.locator(".timeline-legend .seg-away").count() == 1, "the legend explains the away hatching")
            pg.mouse.wheel(0, 900)
            pg.wait_for_timeout(200)
            shot(pg, "uc1-05-yesterday-scrolled")
            settle(pg, "files", lambda: pg.click("text=Files & commits"))
            shot(pg, "uc1-06-files")

        if step("uc4"):
            settle(pg, "search view", lambda: pg.click(".sidebar >> text=Search"))
            shot(pg, "uc4-01-search-empty")
            pg.fill("input[type=text]", "fts5")
            settle(pg, "search fts5", lambda: pg.press("input[type=text]", "Enter"))
            rows = pg.locator("tr.clickable")
            check(rows.count() > 0, f"fts5 finds hits ({rows.count()})")
            shot(pg, "uc4-02-search-fts5")
            first_time = rows.first.locator("td").first.inner_text() if rows.count() else ""
            first_dur = rows.first.locator("td").last.inner_text() if rows.count() else ""
            check(bool(first_dur.strip()), f"a window hit shows how long it was open ({first_dur!r})")
            if rows.count():
                settle(pg, "click first hit", lambda: rows.first.click())
                check("#/today/" in pg.url and "@" in pg.url, f"the hit opened its day at that moment ({pg.url})")
                sel = pg.locator(".timeline-seg.selected")
                check(sel.count() == 1, "the hit's segment is pinned")
                detail = pg.locator(".timeline-detail").inner_text()
                check("FTS5" in detail, f"the pinned segment is the FTS5 page ({detail.splitlines()[:2]})")
                check(first_time in detail, f"at the hit's time {first_time}")
                shot(pg, "uc4-03-hit-opens-its-day")
                url = pg.url
                settle(pg, "reload", lambda: pg.reload())
                check(pg.url == url and pg.locator(".timeline-seg.selected").count() == 1, "reload keeps the day and the pinned moment")
                settle(pg, "back", lambda: pg.go_back())
                check(pg.url.endswith("#/search"), f"Back returns to Search ({pg.url})")
            settle(pg, "search view", lambda: pg.click(".sidebar >> text=Search"))
            pg.fill("input[type=text]", "incógnito")
            settle(pg, "search incognito", lambda: pg.press("input[type=text]", "Enter"))
            check(pg.locator("tr.clickable").count() == 0, "the Spanish private window was never recorded (B1)")
            shot(pg, "uc4-04-search-incognito")
            pg.fill("input[type=text]", "Banco Ejemplo")
            settle(pg, "search bank", lambda: pg.press("input[type=text]", "Enter"))
            check(pg.locator("tr.clickable").count() == 0, "the bank page title is not searchable")

        if step("uc6"):
            settle(pg, "projects", lambda: pg.click("text=Projects"))
            names = [n.strip() for n in pg.locator("table tbody tr td:first-child").all_inner_texts()]
            print(f"  projects: {names}")
            clones = {"python", "tools", "docs", "react", "fastapi", "llama.cpp", "chat", "data"}
            check(bool(names) and not (clones & set(names)), "Projects lists only the user's projects (B3)")
            shot(pg, "uc6-01-projects")
            settle(pg, "files", lambda: pg.click("text=Files & commits"))
            summary = pg.locator("text=repos found").first
            check(summary.count() == 1, f"the watched folder says how many repos and when ({summary.inner_text() if summary.count() else ''})")
            pg.fill("input[placeholder]", str(REPO_ROOT / "data-uxtest" / "does-not-exist"))
            settle(pg, "add missing root", lambda: pg.click("button:has-text('Add')"))
            check(pg.locator("text=is not a folder").count() == 1, "a bad folder is refused with a reason (A2)")
            shot(pg, "uc6-02-add-missing-root")
            settle(pg, "rules", lambda: pg.click("text=Rules"))
            shot(pg, "uc6-03-rules")

        if step("uc5"):
            settle(pg, "privacy", lambda: pg.click("text=Privacy"))
            shot(pg, "uc5-01-privacy")
            btn = pg.locator("button:has-text('Pause 1 hour')")
            if btn.count():
                settle(pg, "pause 1 h", lambda: btn.first.click())
                check("Paused until" in pg.locator(".sidebar").inner_text(), "the sidebar says paused and until when")
                shot(pg, "uc5-02-paused")
            resume = pg.locator("button:has-text('Resume')")
            if resume.count():
                settle(pg, "resume", lambda: resume.first.click())
            settle(pg, "preset last 30 min", lambda: pg.click("button:has-text('Last 30 min')"))
            start = pg.locator("input[type=datetime-local]").nth(0).input_value()
            end = pg.locator("input[type=datetime-local]").nth(1).input_value()
            check(bool(start and end), f"'Last 30 min' fills both pickers ({start} -> {end})")
            pg.locator("text=Delete a time range").scroll_into_view_if_needed()
            shot(pg, "uc5-03-delete-preset")
            with pg.expect_download() as dl:
                pg.click("a:has-text('Spans as CSV')")
            path = dl.value.path()
            text = Path(path).read_text(encoding="utf-8")
            rows = list(csv.DictReader(io.StringIO(text)))
            print(f"  CSV: {dl.value.suggested_filename}, {len(rows)} rows, columns {list(rows[0]) if rows else []}")
            check(len(rows) > 100 and "project" in rows[0], "the CSV export has one row per span with a project column (UC8)")
            check(not any("Banco" in r["title"] or "cógnito" in r["title"] for r in rows), "no redacted or excluded title in the export")
            (REPO_ROOT / "data-uxtest" / "export.csv").write_text(text, encoding="utf-8")

        if step("uc7"):
            settle(pg, "today", lambda: pg.click(".sidebar >> text=Today"))
            shot(pg, "uc7-01-today-after-away")

        if step("misc"):
            for view in ("Week", "Settings", "Assistant activity"):
                settle(pg, view, lambda v=view: pg.click(f".sidebar >> text={v}"))
                shot(pg, f"misc-{view.split()[0].lower()}")
            # keyboard: can the timeline itself be read without a mouse?
            settle(pg, "today", lambda: pg.goto(base + "/#/today"))
            pg.locator(".timeline-seg").first.focus()
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(200)
            focused = pg.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')")
            check(bool(focused) and pg.locator(".timeline-seg.selected").count() == 1, f"a segment takes focus and Enter pins it ({focused!r})")
            pg.keyboard.press("Tab")
            pg.wait_for_timeout(150)
            shot(pg, "misc-keyboard-focus")

        if step("es"):
            es = page_for(1920, 1080, "es-ES")
            settle(es, "open app (es, 1920)", lambda: es.goto(base + "/"))
            check(es.locator("text=¿Dónde lo dejé?").count() == 1, "the card is in Spanish")
            shot(es, "es-01-hoy-1920")
            es.locator(".where-card").scroll_into_view_if_needed()
            shot(es, "es-02-donde-lo-deje")
            for view in ("Semana", "Buscar", "Proyectos", "Archivos y commits", "Privacidad", "Ajustes"):
                settle(es, view, lambda v=view: es.click(f".sidebar >> text={v}"))
                shot(es, f"es-{view.split()[0].lower()}")
            settle(es, "ayer", lambda: es.goto(base + "/#/today"))
            settle(es, "ayer", lambda: es.click("text=Ayer"))
            check(es.locator(".header h2").inner_text() == "Ayer", "the header says Ayer")
            shot(es, "es-ayer-1920")

        if step("locale"):
            # A browser that reports a POSIX locale (this Linux box: en-US@posix)
            # once made every date format call throw and blanked the app.
            raw = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            raw.on("pageerror", lambda e: errors.append(f"{raw.url}: {e}"))
            settle(raw, "open app (browser default locale)", lambda: raw.goto(base + "/"))
            lang = raw.evaluate("navigator.language")
            check(raw.locator(".where-card").count() == 1, f"the app renders with navigator.language={lang!r}")

        browser.close()
    print("console errors:", errors or "none")
    print("failed checks:", failed or "none")
    if failed or errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
