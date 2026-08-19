"""Shared §8 evidence-record payload builder for the Phase 6 unit suites.

A plain helper module (not a test, not a conftest): pytest's prepend import
mode puts this directory on sys.path, so ``from evidence_payloads import
record_dict`` works from sibling test modules. The integration suites never
use it — they build records through the real renderer.
"""

from __future__ import annotations

from typing import Any


def record_dict() -> dict[str, Any]:
    """A complete, valid BUILD-PLAN §8 payload. Fresh dict per call."""
    return {
        "violation_id": "ab12cd34ef567890",
        "constraint": {
            "id": "T1.synchronised_approval",
            "natural_language": "Every approval-requiring decision has a prior authorised grant.",
            "formal": "for every make_decision with outcome in {grant, deny}: ...",
            "ruleset_version": "2026-08.1",
        },
        "legal_basis": [
            {
                "instrument": "Regulation (EU) 2024/1689",
                "article": "14",
                "paragraph": "4(d)",
                "requirement": "Human oversight over high-risk system output.",
            }
        ],
        "standard_refs": [
            {"standard": "ISO/IEC 42001", "control": "A.9.2"},
            {"framework": "NIST AI RMF", "function": "MEASURE"},
        ],
        "severity": "high",
        "evidence": {
            "ocel_event_ids": ["EVT-SESS-0001-025-make_decision"],
            "ocel_object_ids": ["DEC-APP-0123456789"],
            "otel_trace_id": "5f4d7e4c8991a7b690adaf603e2e12bf",
            "otel_span_ids": ["3798dbdac2ca5d97"],
        },
        "context": {
            "policy_version": "2026.02",
            "model_versions": {"claude-haiku-4-5": "2025-10"},
            "agent_versions": {"AGT-INTAKE": "1.0.0"},
        },
        "provenance": {
            "engine_version": "engine/0.1.0",
            "engine_commit": "",
            "input_log_sha256": "0" * 63 + "e",
        },
        "reproducibility": {
            "rerun_command": (
                "python -m auditors_trace.cli check --log " + "0" * 63 + "e --rules 2026-08.1"
            )
        },
        "retention": {
            "class": "ai_act_art_26_6",
            "min_retention_months": 6,
            "note": ("financial institutions: retain per applicable Union financial services law"),
        },
    }
