#!/usr/bin/env python3
"""Capture the screenshots under docs/media from a running --demo instance.

Usage (from the repo root, with the venv active and the frontend built):

    python -m funes_hoard --demo --no-browser --port 18836 &
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python scripts/screenshots.py --port 18836

Requires `playwright` and its chromium browser (not installed by this repo's
own requirements-lock.txt -- it is a one-off dev tool, not a runtime dep).
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = REPO_ROOT / "docs" / "media"


def _agent(base: str, tool: str, args: dict) -> None:
    """Call the agent API the way the MCP adapter does, so the "Assistant
    activity" screen shows real audited calls (including a rejected one)."""
    req = urllib.request.Request(
        f"{base}/api/agent/{tool}", data=json.dumps(args).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except urllib.error.HTTPError:
        pass  # a 400 is part of the demo: the audit log shows failures too


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8813)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    for tool, tool_args in (
        ("activity_where_was_i", {"before": "yesterday", "contexts": 3}),
        ("activity_summary", {"day": "this week", "group_by": "project"}),
        ("activity_search", {"query": "duckdb", "since": "-3d"}),
        ("activity_timeline", {"start": "the other day"}),
        ("activity_timeline", {"start": "yesterday", "limit": 10}),
        ("activity_now", {}),
    ):
        _agent(base, tool, tool_args)

    with sync_playwright() as p:
        browser = p.chromium.launch(timeout=600_000)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, locale="en-GB")

        page.goto(base + "/", wait_until="networkidle")
        page.click("text=Yesterday")
        page.wait_for_timeout(900)
        seg = page.locator(".timeline-seg").nth(3)
        seg.click()
        page.wait_for_timeout(300)
        page.mouse.move(10, 890)
        page.screenshot(path=str(MEDIA_DIR / "today.png"))

        page.click("text=Privacy")
        page.wait_for_timeout(500)
        page.click("button:has-text('Last 30 min')")  # the delete-range shortcut, filled but not confirmed
        page.mouse.move(10, 890)
        page.screenshot(path=str(MEDIA_DIR / "privacy.png"))

        page.click("text=Search")
        page.wait_for_selector("input[type=text]")
        page.fill("input[type=text]", "duckdb")
        page.press("input[type=text]", "Enter")
        page.wait_for_timeout(700)
        page.screenshot(path=str(MEDIA_DIR / "search.png"))

        page.click("text=Assistant activity")
        page.wait_for_timeout(500)
        page.screenshot(path=str(MEDIA_DIR / "assistant-activity.png"))

        page.click("text=Settings")
        page.wait_for_timeout(500)
        page.screenshot(path=str(MEDIA_DIR / "settings.png"))

        browser.close()

    print(f"Wrote screenshots to {MEDIA_DIR}")


if __name__ == "__main__":
    main()
