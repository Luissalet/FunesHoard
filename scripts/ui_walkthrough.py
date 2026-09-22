#!/usr/bin/env python3
"""Walk the person use cases (docs/USE_CASES.md) in a real browser.

    python scripts/uxtest_data.py serve --port 18830 &
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python scripts/ui_walkthrough.py --port 18830

Screenshots go to data-uxtest/shots/ (gitignored) and are meant to be
looked at, not just produced: each step prints what it did, how long the
screen took to settle and any console error. Needs `playwright` in the
interpreter that runs it (a dev tool, not a runtime dependency).
"""
from __future__ import annotations

import argparse
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

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page_for(w: int, h: int, locale: str):
            ctx = browser.new_context(viewport={"width": w, "height": h}, locale=locale)
            pg = ctx.new_page()
            pg.on("console", lambda m: m.type == "error" and errors.append(f"{pg.url}: {m.text}"))
            pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e}"))
            return pg

        def settle(pg, what: str, fn) -> float:
            t0 = time.perf_counter()
            fn()
            pg.wait_for_load_state("networkidle")
            # React fetches after the first paint: wait until no request is
            # in flight and no view is still in its loading state.
            pg.wait_for_function("() => !document.querySelector('button[disabled].btn-primary')", timeout=5000)
            pg.wait_for_timeout(350)
            dt = time.perf_counter() - t0
            print(f"  {what}: {dt * 1000:.0f} ms")
            return dt

        def shot(pg, name: str) -> None:
            pg.screenshot(path=str(out / f"{name}.png"))
            print(f"  -> {name}.png")

        def step(name: str) -> bool:
            run = not only or any(name.startswith(o) for o in only)
            if run:
                print(f"[{name}]")
            return run

        pg = page_for(1280, 800, "en-GB")

        if step("uc1"):
            settle(pg, "open app", lambda: pg.goto(base + "/"))
            pg.wait_for_timeout(500)
            shot(pg, "uc1-01-today-1280")
            settle(pg, "yesterday", lambda: pg.click("text=Yesterday"))
            pg.wait_for_timeout(400)
            shot(pg, "uc1-02-yesterday")
            segs = pg.locator(".timeline-seg")
            print(f"  timeline segments: {segs.count()}")
            if segs.count():
                segs.nth(segs.count() - 1).click()
                pg.wait_for_timeout(200)
                shot(pg, "uc1-03-last-segment")
            pg.mouse.wheel(0, 900)
            pg.wait_for_timeout(200)
            shot(pg, "uc1-04-yesterday-scrolled")
            settle(pg, "files", lambda: pg.click("text=Files & commits"))
            shot(pg, "uc1-05-files")

        if step("uc4"):
            settle(pg, "search view", lambda: pg.click("text=Search"))
            shot(pg, "uc4-01-search-empty")
            pg.fill("input[type=text]", "fts5")
            settle(pg, "search fts5", lambda: pg.press("input[type=text]", "Enter"))
            shot(pg, "uc4-02-search-fts5")
            pg.fill("input[type=text]", "sqlite fts extension")
            settle(pg, "search 3 words", lambda: pg.press("input[type=text]", "Enter"))
            first = pg.locator("td.snippet").first
            if first.count():
                first.click()
                pg.wait_for_timeout(300)
            shot(pg, "uc4-03-after-click-hit")
            pg.fill("input[type=text]", "incógnito")
            settle(pg, "search incognito", lambda: pg.press("input[type=text]", "Enter"))
            shot(pg, "uc4-04-search-incognito")

        if step("uc6"):
            settle(pg, "projects", lambda: pg.click("text=Projects"))
            shot(pg, "uc6-01-projects")
            settle(pg, "files", lambda: pg.click("text=Files & commits"))
            pg.fill("input[placeholder]", str(REPO_ROOT / "data-uxtest" / "does-not-exist"))
            settle(pg, "add missing root", lambda: pg.click("button:has-text('Add')"))
            shot(pg, "uc6-02-add-missing-root")
            settle(pg, "rules", lambda: pg.click("text=Rules"))
            shot(pg, "uc6-03-rules")

        if step("uc5"):
            settle(pg, "privacy", lambda: pg.click("text=Privacy"))
            shot(pg, "uc5-01-privacy")
            btn = pg.locator("button:has-text('1 h')")
            if btn.count():
                settle(pg, "pause 1 h", lambda: btn.first.click())
                pg.wait_for_timeout(500)
                shot(pg, "uc5-02-paused")
            resume = pg.locator("button:has-text('Resume')")
            if resume.count():
                settle(pg, "resume", lambda: resume.first.click())
            pg.mouse.wheel(0, 900)
            pg.wait_for_timeout(200)
            shot(pg, "uc5-03-privacy-scrolled")

        if step("uc7"):
            settle(pg, "today", lambda: pg.click("text=Today"))
            pg.wait_for_timeout(300)
            shot(pg, "uc7-01-today-after-away")

        if step("misc"):
            for view in ("Week", "Settings", "Assistant activity"):
                settle(pg, view, lambda v=view: pg.click(f"text={v}"))
                pg.wait_for_timeout(300)
                shot(pg, f"misc-{view.split()[0].lower()}")
            # keyboard: can the sidebar be reached and used without a mouse?
            pg.goto(base + "/")
            pg.wait_for_load_state("networkidle")
            for _ in range(4):
                pg.keyboard.press("Tab")
            focused = pg.evaluate("document.activeElement && (document.activeElement.innerText || document.activeElement.getAttribute('aria-label'))")
            print(f"  after 4x Tab, focus on: {focused!r}")
            shot(pg, "misc-keyboard-focus")

        if step("es"):
            es = page_for(1920, 1080, "es-ES")
            settle(es, "open app (es, 1920)", lambda: es.goto(base + "/"))
            es.wait_for_timeout(500)
            shot(es, "es-01-hoy-1920")
            for view in ("Semana", "Proyectos", "Archivos y commits", "Privacidad", "Ajustes"):
                settle(es, view, lambda v=view: es.click(f"text={v}"))
                es.wait_for_timeout(300)
                shot(es, f"es-{view.split()[0].lower()}")

        browser.close()
    print("console errors:", errors or "none")


if __name__ == "__main__":
    main()
