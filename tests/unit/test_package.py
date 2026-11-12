"""Package smoke tests: the skeleton imports cleanly and reports its version."""

import app
from app import __version__


def test_package_imports() -> None:
    assert app.__name__ == "app"


def test_version_matches_project() -> None:
    assert __version__ == "1.0.0"
