"""`python -m funes_hoard` -- run the app with uvicorn on 127.0.0.1.

Flags: --port, --data-dir, --demo, --no-browser
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from funes_hoard import __version__
from funes_hoard.api import PID_FILE, create_app

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
    # Loopback only: the app holds the user's screen history and has no
    # authentication, so it must never listen on a LAN interface.
    parser.add_argument("--host", type=str, default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
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

    _write_pid_file(data_dir, args.port)
    print(f"Funes's Hoard v{__version__} - {data_dir} - http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _write_pid_file(data_dir: Path, port: int) -> None:
    """scripts/stop.ps1 reads this. On Windows the venv's python.exe is a
    launcher that runs the real interpreter as a child, so the launcher's
    PID (what Start-Process returns) is not the process holding the port.
    The app removes the file on a graceful shutdown (see api.lifespan)."""
    try:
        (data_dir / PID_FILE).write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    main()
