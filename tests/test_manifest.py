from pathlib import Path

from tests.faustus_manifest import check_repo

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_is_valid():
    data = check_repo(REPO_ROOT)
    assert data["id"] == "funes"
    assert data["mcp"]["transport"] == "stdio"
