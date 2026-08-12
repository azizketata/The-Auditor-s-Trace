"""Phase 0: the skeleton holds together.

Importing every module catches syntax errors and circular imports across the
whole package, which is the only thing there is to verify before Phase 1.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import auditors_trace

EXPECTED_MODULES = {
    "auditors_trace.baselines.declare_flat",
    "auditors_trace.baselines.llm_judge",
    "auditors_trace.baselines.ocdfg",
    "auditors_trace.cli",
    "auditors_trace.constraints.engine",
    "auditors_trace.constraints.ruleset",
    "auditors_trace.constraints.standard",
    "auditors_trace.constraints.templates",
    "auditors_trace.eval.figures",
    "auditors_trace.eval.metrics",
    "auditors_trace.eval.runner",
    "auditors_trace.evidence.chain",
    "auditors_trace.evidence.crosswalk",
    "auditors_trace.evidence.record",
    "auditors_trace.evidence.renderer",
    "auditors_trace.ingest.attribute_map",
    "auditors_trace.ingest.mapper",
    "auditors_trace.ingest.otel_reader",
    "auditors_trace.model.io",
    "auditors_trace.model.log",
    "auditors_trace.model.ocel_schema",
    "auditors_trace.scenario.__main__",
    "auditors_trace.scenario.agents",
    "auditors_trace.scenario.injector",
    "auditors_trace.scenario.seed",
}


def _discovered_modules() -> set[str]:
    return {
        info.name
        for info in pkgutil.walk_packages(auditors_trace.__path__, "auditors_trace.")
        if not info.ispkg
    }


def test_version_is_set() -> None:
    assert auditors_trace.__version__ == "0.1.0"


def test_every_expected_module_is_present() -> None:
    assert EXPECTED_MODULES <= _discovered_modules()


@pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
def test_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
