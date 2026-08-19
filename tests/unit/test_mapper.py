"""Phase 3: the two-pass span->OCEL mapper, span index, and its refusals.

Hermetic: consumes only the committed golden span fixtures, the span
contract, and the Phase 1 model — no pm4py, no scenario extra.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.ingest.mapper import MappedRun, map_span_trees
from auditors_trace.ingest.otel_reader import (
    IngestError,
    build_span_trees,
    read_spans,
)
from auditors_trace.model.log import OCELModelError, log_hash
from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import SpanContractError

SPANS = Path(__file__).resolve().parent.parent / "golden" / "spans"
GRANT = SPANS / "credit_grant_approval_seed42.jsonl"
DENY = SPANS / "credit_deny_approval_refer_seed2.jsonl"
MESSY = SPANS / "messy_vendor_variants.jsonl"


def _map(path: Path) -> MappedRun:
    return map_span_trees(build_span_trees(read_spans(path)))


def _rewrite(src: Path, tmp_path: Path, mutate: Any, name: str = "") -> Path:
    rows = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines()]
    out = tmp_path / (name or src.name)
    out.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in mutate(rows)),
        encoding="utf-8",
        newline="\n",
    )
    return out


class TestGoldenSessionMaps:
    def test_shape_matches_the_span_census(self) -> None:
        run = _map(GRANT)
        assert len(run.log.events) == 28
        # 24 declared-with-relation + 1 pure declaration = 25 objects.
        assert len(run.log.objects) == 25
        # 92 relation-only + 24 relation+declaration + 1 synthesized declares.
        assert sum(len(e.relations) for e in run.log.events) == 117

    def test_handoff_spans_produce_agent_to_agent_o2o(self) -> None:
        run = _map(GRANT)
        triples = {(r.source_id, r.target_id, r.qualifier) for r in run.log.o2o}
        delegations = {t for t in triples if t[2] is Qualifier.DELEGATES_TO}
        assert delegations == {
            ("AGT-INTAKE", "AGT-RETRIEVAL", Qualifier.DELEGATES_TO),
            ("AGT-RETRIEVAL", "AGT-SCORING", Qualifier.DELEGATES_TO),
            ("AGT-SCORING", "AGT-ADJUDICATION", Qualifier.DELEGATES_TO),
        }
        assert sum(1 for t in triples if t[2] is Qualifier.SUBMITTED_BY) == 1
        assert sum(1 for t in triples if t[2] is Qualifier.DERIVED_FROM) == 1

    def test_pure_declaration_gets_a_synthesized_declares_relation(self) -> None:
        run = _map(GRANT)
        superseded = "POL-CREDIT-2025.11"
        assert any(
            o.object_id == superseded and o.object_type is ObjectType.POLICY_VERSION
            for o in run.log.objects
        )
        relations = [
            (e.event_type, r.qualifier)
            for e in run.log.events
            for r in e.relations
            if r.object_id == superseded
        ]
        assert relations == [(EventType.SESSION_START, Qualifier.DECLARES)]

    def test_timestamps_come_from_the_simulated_clock(self) -> None:
        run = _map(GRANT)
        assert all(e.timestamp.startswith("2026-03-02T09:") for e in run.log.events)

    def test_coverage_is_complete_on_the_golden(self) -> None:
        from auditors_trace.ingest.attribute_map import mapped_fraction

        run = _map(GRANT)
        assert run.coverage.event_count == 28
        assert run.coverage.layer_b_span_count == 13
        assert {k: (p, t) for k, p, t in run.coverage.per_kind}.keys() == {
            "CHAIN",
            "LLM",
            "TOOL",
        }
        assert mapped_fraction(run.coverage) == 1.0
        assert run.coverage.unknown_keys == ()

    def test_span_index_attributes_enrichment_correctly(self) -> None:
        run = _map(GRANT)
        assert len(run.span_index.events) == 28
        assert len(run.span_index.sessions) == 1
        session = run.span_index.sessions[0]
        assert session.session_id == "SESS-0000"
        assert len(session.session_span_ids) == 5  # the LangGraph CHAIN spans
        by_event = {ref.event_id: ref for ref in run.span_index.events}
        spans = {s.span_id: s for s in read_spans(GRANT)}
        for ref in run.span_index.events:
            event_type = ref.event_id.rsplit("-", 1)[-1]
            if event_type in ("call_llm", "call_tool"):
                assert len(ref.enrichment_span_ids) == 1, ref.event_id
            else:
                assert ref.enrichment_span_ids == (), ref.event_id
            for span_id in ref.enrichment_span_ids:
                assert spans[span_id].scope_name.startswith("openinference")
        assert len(by_event) == 28

    def test_deny_golden_maps_with_concerns_not_approves(self) -> None:
        run = _map(DENY)
        deny_events = [e for e in run.log.events if e.event_type is EventType.DENY_APPROVAL]
        assert len(deny_events) == 1
        decision_quals = {
            r.qualifier for r in deny_events[0].relations if r.object_id.startswith("DEC-")
        }
        assert decision_quals == {Qualifier.CONCERNS}


def test_both_vocabularies_map_to_same_ocel() -> None:
    """The gen_ai.* and openinference/llm.* spellings of one session produce
    identical OCEL, identical span index, and identical coverage."""
    from auditors_trace.ingest.attribute_map import coverage_json, mapped_fraction
    from auditors_trace.ingest.mapper import span_index_json

    runs = [
        _map(SPANS / name)
        for name in (
            "paired_vocabulary_genai.jsonl",
            "paired_vocabulary_openinference.jsonl",
        )
    ]
    assert log_hash(runs[0].log) == log_hash(runs[1].log)
    assert span_index_json(runs[0].span_index) == span_index_json(runs[1].span_index)
    assert coverage_json(runs[0].coverage, None) == coverage_json(runs[1].coverage, None)
    assert mapped_fraction(runs[0].coverage) == 1.0
    assert len(runs[0].log.events) == 5


class TestMapperRefusals:
    def test_full_messy_file_raises_never_drops(self) -> None:
        with pytest.raises(SpanContractError):
            _map(MESSY)

    def test_malformed_encoding_raises(self, tmp_path: Path) -> None:
        # Only the root and the malformed call_tool span (obj.count mismatch).
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if r["creation_index"] in (1, 6)]

        with pytest.raises(SpanContractError, match=r"at\.obj"):
            _map(_rewrite(MESSY, tmp_path, mutate))

    def test_actor_policy_violation_raises(self, tmp_path: Path) -> None:
        # Only the root and the parse-valid invoke_agent missing its actor.
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if r["creation_index"] in (1, 5)]

        with pytest.raises(SpanContractError, match="actor"):
            _map(_rewrite(MESSY, tmp_path, mutate))

    def test_duplicate_session_id_across_traces_raises(self) -> None:
        # Both goldens use SESS-0000 — mapping them together must fail loudly.
        spans = read_spans(GRANT) + read_spans(DENY)
        with pytest.raises(IngestError, match="session"):
            map_span_trees(build_span_trees(spans))

    def test_seq_gap_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if r["attributes"].get("at.event.seq") != 14]

        with pytest.raises(IngestError, match="seq"):
            _map(_rewrite(GRANT, tmp_path, mutate))

    def test_duplicate_seq_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for row in rows:
                if row["attributes"].get("at.event.seq") == 14:
                    row["attributes"]["at.event.seq"] = 13
            return rows

        with pytest.raises(IngestError, match="seq"):
            _map(_rewrite(GRANT, tmp_path, mutate))

    def test_cross_trace_declaration_conflict_raises(self, tmp_path: Path) -> None:
        # A second session that redeclares AGT-INTAKE with a different version.
        text = GRANT.read_text(encoding="utf-8")
        text = text.replace("SESS-0000", "SESS-0001")
        text = text.replace("e2f2b80b7d3bb82ca7cdcbb612e36a77", "00000000000000000000000000000bbb")
        rows = [json.loads(line) for line in text.splitlines()]
        for row in rows:
            attrs = row["attributes"]
            if attrs.get("at.event.type") != "invoke_agent":
                continue
            for key, value in list(attrs.items()):
                if key.endswith(".attr.version") and value == "1.0.0":
                    attrs[key] = "9.9.9"
        second = tmp_path / "second.jsonl"
        second.write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8",
            newline="\n",
        )
        spans = read_spans(GRANT) + read_spans(second)
        with pytest.raises(IngestError, match="redeclar"):
            map_span_trees(build_span_trees(spans))

    def test_unknown_event_attribute_is_rejected_by_the_model(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for row in rows:
                if row["attributes"].get("at.event.seq") == 2:
                    row["attributes"]["at.event.attr.frobnication_level"] = 9
            return rows

        with pytest.raises(OCELModelError, match="frobnication_level"):
            _map(_rewrite(GRANT, tmp_path, mutate))


class TestReviewFindings:
    """Regressions for the adversarial review, 19 Aug 2026."""

    def _two_trace_rows(
        self, first_trace: str, second_trace: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The grant golden twice: second copy re-identified as another session."""
        text = GRANT.read_text(encoding="utf-8")
        first = [json.loads(line) for line in text.splitlines()]
        second_text = text.replace("SESS-0000", "SESS-0001").replace(
            "e2f2b80b7d3bb82ca7cdcbb612e36a77", second_trace
        )
        second = [json.loads(line) for line in second_text.splitlines()]
        first_text = text.replace("e2f2b80b7d3bb82ca7cdcbb612e36a77", first_trace)
        first = [json.loads(line) for line in first_text.splitlines()]
        return first, second

    def _write(self, tmp_path: Path, name: str, rows: list[dict[str, Any]]) -> Path:
        out = tmp_path / name
        out.write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8",
            newline="\n",
        )
        return out

    @pytest.mark.parametrize(
        ("first_trace", "second_trace"),
        [
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        ],
        ids=["declarer-sorts-second", "declarer-sorts-first"],
    )
    def test_type_conflicting_redeclaration_raises_in_both_orders(
        self, first_trace: str, second_trace: str, tmp_path: Path
    ) -> None:
        """int 2 vs float 2.0 compare equal in Python, so the old tuple ==
        comparison silently let the first-sorted declaration win; the
        conflict must raise regardless of trace order."""
        first, second = self._two_trace_rows(first_trace, second_trace)
        mutated = 0
        for row in second:
            attrs = row["attributes"]
            if attrs.get("at.event.type") != "request_approval":
                continue
            for key, value in list(attrs.items()):
                if (
                    "at.obj." in key
                    and key.endswith(".attr.authority_level")
                    and isinstance(value, int)
                ):
                    attrs[key] = float(value)  # equal under ==, different type
                    mutated += 1
        assert mutated >= 1
        # The shared HumanApprover keeps its id across sessions, so the two
        # traces now redeclare it with ==-equal but type-different attributes.
        spans = read_spans(self._write(tmp_path, "first.jsonl", first)) + read_spans(
            self._write(tmp_path, "second.jsonl", second)
        )
        with pytest.raises(IngestError, match="redeclar"):
            map_span_trees(build_span_trees(spans))

    def test_zero_event_session_raises(self, tmp_path: Path) -> None:
        """A session root with no events must not map silently — the dense-seq
        gate would pass vacuously and the lost session would be invisible."""
        rows = [json.loads(line) for line in GRANT.read_text(encoding="utf-8").splitlines()]
        root_only = [r for r in rows if r["attributes"].get("at.span.role") == "session_root"]
        with pytest.raises(IngestError, match="no event spans"):
            _map(self._write(tmp_path, "root_only.jsonl", root_only))

    def test_undeclared_reference_check_is_order_independent(self, tmp_path: Path) -> None:
        """An object referenced in one trace but declared only in another maps
        identically whichever trace id sorts first (the old per-trace check
        accepted or rejected depending on lexicographic trace-id order)."""
        from dataclasses import replace as dc_replace

        from auditors_trace.model.span_contract import (
            ObjectRef,
            flatten_event,
            parse_event,
        )

        foreign_id = "DEC-APP-ffffffffff"
        hashes = []
        for first_trace, second_trace in (
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        ):
            first, second = self._two_trace_rows(first_trace, second_trace)
            # Give the second trace's decision a unique id, so it is declared
            # ONLY there.
            second = [
                json.loads(json.dumps(r).replace("DEC-APP-bc438fbf25", foreign_id)) for r in second
            ]
            # The first trace's session_end references that foreign decision.
            for row in first:
                attrs = row["attributes"]
                if attrs.get("at.event.type") != "session_end":
                    continue
                spec = parse_event(attrs)
                assert spec is not None
                foreign = ObjectRef(
                    object_type=ObjectType.CREDIT_DECISION,
                    object_id=foreign_id,
                    qualifier=Qualifier.CONCERNS,
                )
                new_spec = dc_replace(spec, objects=(*spec.objects, foreign))
                base = {
                    k: v
                    for k, v in attrs.items()
                    if not k.startswith(("at.event.", "at.obj.", "at.o2o."))
                }
                base.update(flatten_event(new_spec))
                row["attributes"] = base
            spans = read_spans(
                self._write(tmp_path, f"f_{first_trace[:2]}.jsonl", first)
            ) + read_spans(self._write(tmp_path, f"s_{first_trace[:2]}.jsonl", second))
            run = map_span_trees(build_span_trees(spans))
            hashes.append(log_hash(run.log))
        assert hashes[0] == hashes[1]


class TestFaultedButMappable:
    """Pin the acceptance path Phase 4's span-level injection depends on (B1):
    a V1-style fault (decision without approval) IS mappable after the full
    injection surgery — drop the approval events, renumber seq densely, and
    strip every reference to the approval objects."""

    def test_v1_surgery_maps(self, tmp_path: Path) -> None:
        from dataclasses import replace as dc_replace

        from auditors_trace.model.span_contract import flatten_event, parse_event

        rows = [json.loads(line) for line in GRANT.read_text(encoding="utf-8").splitlines()]
        surgered: list[dict[str, Any]] = []
        for row in rows:
            attrs = row["attributes"]
            spec = parse_event(attrs)
            if spec is None:
                surgered.append(row)
                continue
            if spec.seq in (23, 24):  # request_approval, grant_approval
                continue
            new_seq = spec.seq - 2 if spec.seq > 24 else spec.seq
            objects = tuple(r for r in spec.objects if not r.object_id.startswith("APRV-"))
            new_spec = dc_replace(spec, seq=new_seq, objects=objects)
            base = {
                k: v
                for k, v in attrs.items()
                if not k.startswith(("at.event.", "at.obj.", "at.o2o."))
            }
            base.update(flatten_event(new_spec))
            new_row = dict(row)
            new_row["attributes"] = base
            surgered.append(new_row)

        out = tmp_path / "v1_faulted.jsonl"
        out.write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in surgered),
            encoding="utf-8",
            newline="\n",
        )
        run = _map(out)
        assert len(run.log.events) == 26
        assert len(run.log.objects) == 23  # Approval and HumanApprover are gone
        types = {e.event_type for e in run.log.events}
        assert EventType.REQUEST_APPROVAL not in types
        assert EventType.GRANT_APPROVAL not in types
        assert EventType.MAKE_DECISION in types  # the unapproved decision remains


class TestDeterminismSmoke:
    def test_two_reads_one_hash(self) -> None:
        assert log_hash(_map(GRANT).log) == log_hash(_map(GRANT).log)
