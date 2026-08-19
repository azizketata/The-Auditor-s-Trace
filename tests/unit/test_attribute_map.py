"""Phase 3: vocabulary normalisation and the C2 coverage report.

The strongest machine-checkable form of "recorded, never dropped" is a
partition invariant: every raw attribute key of every span ends up in
exactly one of consumed / recorded / unknown. It is asserted here over
every committed fixture span.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from auditors_trace.ingest.attribute_map import (
    CoverageReport,
    NormalisedSpan,
    build_coverage,
    coverage_json,
    mapped_fraction,
    normalise_attributes,
)
from auditors_trace.ingest.otel_reader import IngestError, read_spans
from auditors_trace.model.ocel_schema import EventType

SPANS = Path(__file__).resolve().parent.parent / "golden" / "spans"
FIXTURES = [
    SPANS / "credit_grant_approval_seed42.jsonl",
    SPANS / "credit_deny_approval_refer_seed2.jsonl",
    SPANS / "messy_vendor_variants.jsonl",
    SPANS / "paired_vocabulary_genai.jsonl",
    SPANS / "paired_vocabulary_openinference.jsonl",
]

GENAI_LLM = {
    "gen_ai.conversation.id": "SESS-PAIR",
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "scripted-credit-policy-v1",
    "gen_ai.provider.name": "anthropic",
    "gen_ai.request.temperature": 0.0,
    "gen_ai.request.max_tokens": 512,
    "gen_ai.usage.input_tokens": 41,
    "gen_ai.usage.output_tokens": 63,
}
OPENINFERENCE_LLM = {
    "session.id": "SESS-PAIR",
    "openinference.span.kind": "LLM",
    "llm.model_name": "scripted-credit-policy-v1",
    "llm.provider": "anthropic",
    "llm.invocation_parameters": json.dumps(
        {"max_tokens": 512, "temperature": 0.0}, sort_keys=True
    ),
    "llm.token_count.prompt": 41,
    "llm.token_count.completion": 63,
    "llm.token_count.total": 104,
}


class TestNormaliseAttributes:
    def test_paired_vocabularies_yield_identical_canonical(self) -> None:
        a = normalise_attributes(GENAI_LLM)
        b = normalise_attributes(OPENINFERENCE_LLM)
        assert a.kind == b.kind == "LLM"
        assert a.canonical == b.canonical
        canonical = dict(a.canonical)
        assert canonical["model.name"] == "scripted-credit-policy-v1"
        assert canonical["session.id"] == "SESS-PAIR"
        assert canonical["tokens.total"] == 104  # derived for gen_ai, explicit for llm

    def test_tool_pair(self) -> None:
        genai = {
            "gen_ai.conversation.id": "SESS-PAIR",
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "scorecard",
            "gen_ai.tool.description": "Deterministic scorecard.",
        }
        openinference = {
            "session.id": "SESS-PAIR",
            "openinference.span.kind": "TOOL",
            "tool.name": "scorecard",
            "tool.description": "Deterministic scorecard.",
        }
        a = normalise_attributes(genai)
        b = normalise_attributes(openinference)
        assert a.kind == b.kind == "TOOL"
        assert a.canonical == b.canonical

    def test_kind_falls_back_to_unknown(self) -> None:
        assert normalise_attributes({"custom.vendor.tool": "x"}).kind == "UNKNOWN"

    def test_alias_conflict_raises(self) -> None:
        clash = dict(OPENINFERENCE_LLM)
        clash["gen_ai.request.model"] = "some-other-model"
        with pytest.raises(IngestError, match=r"model\.name"):
            normalise_attributes(clash)

    def test_explicit_total_not_matching_sum_raises(self) -> None:
        clash = dict(OPENINFERENCE_LLM)
        clash["llm.token_count.total"] = 999
        with pytest.raises(IngestError, match=r"tokens\.total"):
            normalise_attributes(clash)

    def test_partition_invariant_on_every_committed_fixture_span(self) -> None:
        for path in FIXTURES:
            for span in read_spans(path):
                normalised = normalise_attributes(span.attributes)
                raw = set(span.attributes)
                consumed = set(normalised.consumed)
                recorded = set(normalised.recorded)
                unknown = set(normalised.unknown)
                assert consumed | recorded | unknown == raw, span.source
                assert not consumed & recorded, span.source
                assert not consumed & unknown, span.source
                assert not recorded & unknown, span.source


def test_unknown_attributes_are_recorded_not_dropped() -> None:
    spans = {s.creation_index: s for s in read_spans(SPANS / "messy_vendor_variants.jsonl")}

    # The renamed-vendor span: every key survives, verbatim, as unknown.
    vendor = normalise_attributes(spans[4].attributes)
    assert set(vendor.unknown) == {
        "custom.vendor.model_identifier",
        "custom.vendor.session",
        "custom.vendor.tool",
    }
    assert vendor.canonical == ()

    # The gen_ai-only span normalises fully.
    genai_only = normalise_attributes(spans[2].attributes)
    assert genai_only.kind == "LLM"
    assert dict(genai_only.canonical)["model.name"] == "vendor-model-x"
    assert genai_only.unknown == ()

    # The partial OpenInference span degrades visibly, never silently.
    partial = normalise_attributes(spans[3].attributes)
    assert set(partial.expected_missing) == {
        "model.provider",
        "invocation.temperature",
        "invocation.max_tokens",
    }
    assert dict(partial.canonical)["tokens.total"] == 42  # derived 30 + 12

    report = build_coverage([], [vendor, genai_only, partial])
    unknown_keys = dict(report.unknown_keys)
    assert unknown_keys["custom.vendor.model_identifier"] == 1
    assert mapped_fraction(report) < 1.0


class TestCoverageReport:
    def _layer_a(self) -> list[tuple[EventType, frozenset[str]]]:
        from auditors_trace.model.span_contract import governance_census

        rows: list[tuple[EventType, frozenset[str]]] = []
        for event_type in (EventType.SESSION_START, EventType.MAKE_DECISION):
            rows.append((event_type, frozenset(governance_census(event_type))))
        return rows

    def test_full_coverage_is_one(self) -> None:
        report = build_coverage(self._layer_a(), [normalise_attributes(GENAI_LLM)])
        assert mapped_fraction(report) == 1.0
        assert report.census_present == report.census_total

    def test_json_is_canonical_and_order_insensitive(self) -> None:
        layer_b = [
            normalise_attributes(GENAI_LLM),
            normalise_attributes(OPENINFERENCE_LLM),
            normalise_attributes({"custom.vendor.tool": "x"}),
        ]
        reports: set[str] = set()
        rng = random.Random(0)
        for _ in range(5):
            shuffled_a = list(self._layer_a())
            shuffled_b = list(layer_b)
            rng.shuffle(shuffled_a)
            rng.shuffle(shuffled_b)
            reports.add(coverage_json(build_coverage(shuffled_a, shuffled_b), None))
        assert len(reports) == 1
        payload = json.loads(reports.pop())
        assert payload["schema_version"] == 1
        assert payload["run"] is None

    def test_report_is_a_frozen_dataclass(self) -> None:
        report = build_coverage([], [])
        assert isinstance(report, CoverageReport)
        assert isinstance(normalise_attributes({}), NormalisedSpan)
        with pytest.raises(AttributeError):
            report.event_count = 5  # type: ignore[misc]
