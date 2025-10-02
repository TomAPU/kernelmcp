# tests/conftest.py
import pytest
import os
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption(
        "--ezqm-local",
        action="store",
        default=None,
        help="Path to ezqm local folder (containing ezqmlocal.json). If not given, VM tests are skipped.",
    )


@pytest.fixture(scope="session")
def ezqm_localfolder(request):
    folder = request.config.getoption(
        "--ezqm-local") or os.environ.get("EZQM_LOCAL")
    if not folder:
        pytest.skip(
            "No ezqm local folder provided (use --ezqm-local=PATH or set EZQM_LOCAL).")
    p = Path(folder)
    if not (p.exists() and (p / 'ezqmlocal.json').exists()):
        pytest.skip(f"Invalid ezqm local folder: {folder}")
    return str(p)
