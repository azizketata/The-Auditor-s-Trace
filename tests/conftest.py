"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent
