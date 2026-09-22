"""`python -m funes_hoard` is what the launchers and Faustus run."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(*args):
    return subprocess.run([sys.executable, "-m", "funes_hoard", *args], cwd=REPO, capture_output=True, text=True, timeout=60)


def test_module_entry_point_runs():
    r = _run("--help")
    assert r.returncode == 0
    assert "--demo" in r.stdout and "--no-browser" in r.stdout


def test_refuses_to_listen_on_a_lan_interface():
    r = _run("--host", "0.0.0.0", "--no-browser")
    assert r.returncode != 0
    assert "invalid choice" in r.stderr
