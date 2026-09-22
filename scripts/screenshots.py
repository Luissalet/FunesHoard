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
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = REPO_ROOT / "docs" / "media"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8813)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)

        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(path=str(MEDIA_DIR / "today.png"))

        page.click("text=Privacy")
        page.wait_for_timeout(500)
        page.screenshot(path=str(MEDIA_DIR / "privacy.png"))

        page.click("text=Search")
        page.wait_for_selector("input[type=text]")
        page.fill("input[type=text]", "Atlas")
        page.press("input[type=text]", "Enter")
        page.wait_for_timeout(700)
        page.screenshot(path=str(MEDIA_DIR / "search.png"))

        page.click("text=Assistant activity")
        page.wait_for_timeout(500)
        page.screenshot(path=str(MEDIA_DIR / "assistant-activity.png"))

        browser.close()

    print(f"Wrote screenshots to {MEDIA_DIR}")


if __name__ == "__main__":
    main()
