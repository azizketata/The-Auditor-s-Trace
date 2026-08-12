"""Phase 0: the modules invariant I1 constrains are importable.

The real determinism suite arrives with Phase 5 (engine) and Phase 6 (evidence
chain): identical violation sets across 100 runs, identical record hashes across
runs. There is nothing to run twice yet.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    ["auditors_trace.constraints.engine", "auditors_trace.evidence.chain"],
)
def test_deterministic_path_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


def test_genesis_hash_is_64_zeros() -> None:
    from auditors_trace.evidence.chain import GENESIS_HASH

    assert GENESIS_HASH == "0" * 64
