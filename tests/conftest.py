"""Shared fixtures.

Seeding the three synthetic demo days drives the real collector for ~1700
simulated minutes, which costs about a second. The API tests need that data
but not a fresh generation each time, so it is generated once per session
and the SQLite file is copied into each test's own data dir.
"""
import shutil

import pytest

from funes_hoard.db import Database
from funes_hoard.demo import seed_demo_data


@pytest.fixture(scope="session")
def _demo_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo-template")
    db = Database(path)
    seed_demo_data(db)
    db.close()  # checkpoints the WAL into the main file
    return path / "funes.sqlite3"


@pytest.fixture()
def demo_data_dir(tmp_path, _demo_template):
    """A private data dir that already holds the demo days."""
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy2(_demo_template, data / "funes.sqlite3")
    return data
