"""Phase 0: the golden-file trees exist for the phases that will fill them."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize("subdir", ["mapper", "evidence"])
def test_golden_subdir_exists(repo_root: Path, subdir: str) -> None:
    assert (repo_root / "tests" / "golden" / subdir).is_dir()
