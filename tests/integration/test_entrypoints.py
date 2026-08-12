"""Phase 0: both documented entrypoints resolve.

`auditors_trace.cli` backs the rerun command every evidence record cites
(section 8); `auditors_trace.scenario.__main__` backs the Phase 2 fleet runner.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    ["auditors_trace.cli", "auditors_trace.scenario.__main__"],
)
def test_entrypoint_exposes_main(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert callable(module.main)
