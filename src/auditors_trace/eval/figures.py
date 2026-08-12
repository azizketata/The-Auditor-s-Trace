"""Paper tables and figures.

Phase 8 deliverable. Writes to ``paper/artefacts/``. matplotlib and pandas are
confined to this module and ``metrics.py``: neither may touch the deterministic
path.
"""

from __future__ import annotations

from pathlib import Path


def generate_all(results_dir: Path, artefacts_dir: Path) -> list[Path]:
    """Generate every table and figure. Returns the paths written, sorted."""
    raise NotImplementedError
