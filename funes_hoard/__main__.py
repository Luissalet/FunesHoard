"""`python -m funes_hoard` -- run the app with uvicorn on 127.0.0.1.

Flags: --port, --data-dir, --demo, --no-browser
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from funes_hoard import __version__
from funes_hoard.api import create_app

DEFAULT_PORT = 8813


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_data_dir(demo: bool) -> Path:
    env_key = "FUNES_DATA_DIR"
    if os.environ.get(env_key):
        return Path(os.environ[env_key])
    return _repo_root() / ("data-demo" if demo else "data")


def _find_static_dir() -> Path | None:
    dist = _repo_root() / "frontend" / "dist"
    return dist if dist.is_dir() else None


def main() -> None:
    parser = argparse.ArgumentParser(prog="funes_hoard", description="Funes's Hoard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir(args.demo)
    static_dir = _find_static_dir()

    app = create_app(data_dir=data_dir, static_dir=static_dir, demo=args.demo, port=args.port)

    if not args.no_browser:
        url = f"http://{args.host}:{args.port}/"

        def _open() -> None:
            time.sleep(1.0)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    print(f"Funes's Hoard v{__version__} - {data_dir} - http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
