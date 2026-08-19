"""Phase 6 acceptance: section 8 conformance, hash chain, tamper evidence,
golden bitwise identity, and ground-truth span agreement.

Hermetic path (CI): one scripted 16-session run -> injected splits (4/8/4,
quota 1 per class) -> mapped in-test WITH the span index intact. Full-scale
path: skip-guarded on data/generated/splits; splits re-mapped from their
manifests (the convenience OCEL under data/generated/ocel can go stale).

The JSON Schema in this file is the implementation-independent pin of
BUILD-PLAN section 8 (additionalProperties: false throughout); pydantic
remains the src-side authority. jsonschema is a dev-only dependency, never
imported by src/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("opentelemetry")

from auditors_trace.constraints.engine import evaluate
from auditors_trace.constraints.ruleset import RuleSet, load_ruleset
from auditors_trace.evidence.chain import GENESIS_HASH, record_hash, sha256_bytes, verify_chain
from auditors_trace.evidence.crosswalk import Crosswalk, load_crosswalk
from auditors_trace.evidence.record import EvidenceRecord
from auditors_trace.evidence.renderer import build_render_index, render_all
from auditors_trace.model.log import log_hash

if TYPE_CHECKING:
    from auditors_trace.ingest.mapper import MappedRun

REPO = Path(__file__).resolve().parent.parent.parent
CATALOGUE = REPO / "data" / "catalogue" / "violations.yaml"
RULES = REPO / "rules" / "rules.yaml"
FIXTURE_CROSSWALK = REPO / "tests" / "fixtures" / "crosswalk_fixture.yaml"
FIXTURE_SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"
GOLDEN = REPO / "tests" / "golden" / "evidence"
GENERATED_SPLITS = REPO / "data" / "generated" / "splits"

SIZES = {"clean": 4, "single": 8, "mixed": 4}

full_scale = pytest.mark.skipif(
    not (GENERATED_SPLITS / "single" / "labels.json").exists(),
    reason="full-scale splits absent; run `make scenario` and `scenario inject` first",
)

_HEX16 = {"type": "string", "pattern": "^[0-9a-f]{16}$"}
_HEX32 = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
_HEX64 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_NON_EMPTY = {"type": "string", "minLength": 1}

#: BUILD-PLAN section 8, transcribed as JSON Schema. The independent pin.
RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "violation_id",
        "constraint",
        "legal_basis",
        "standard_refs",
        "severity",
        "evidence",
        "context",
        "provenance",
        "reproducibility",
        "integrity",
        "retention",
    ],
    "properties": {
        "violation_id": _HEX16,
        "constraint": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "natural_language", "formal", "ruleset_version"],
            "properties": {
                "id": _NON_EMPTY,
                "natural_language": _NON_EMPTY,
                "formal": _NON_EMPTY,
                "ruleset_version": _NON_EMPTY,
            },
        },
        "legal_basis": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["instrument", "article", "requirement"],
                "properties": {
                    "instrument": _NON_EMPTY,
                    "article": _NON_EMPTY,
                    "paragraph": {"type": "string"},
                    "requirement": _NON_EMPTY,
                },
            },
        },
        "standard_refs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "minProperties": 2,
                "maxProperties": 2,
                "properties": {
                    "standard": _NON_EMPTY,
                    "clause": _NON_EMPTY,
                    "control": _NON_EMPTY,
                    "framework": _NON_EMPTY,
                    "function": _NON_EMPTY,
                },
            },
        },
        "severity": {"enum": ["low", "medium", "high"]},
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ocel_event_ids", "ocel_object_ids", "otel_trace_id", "otel_span_ids"],
            "properties": {
                "ocel_event_ids": {"type": "array", "minItems": 1, "items": _NON_EMPTY},
                "ocel_object_ids": {"type": "array", "items": _NON_EMPTY},
                "otel_trace_id": _HEX32,
                "otel_span_ids": {"type": "array", "minItems": 1, "items": _HEX16},
            },
        },
        "context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["policy_version", "model_versions", "agent_versions"],
            "properties": {
                "policy_version": {"type": "string"},
                "model_versions": {"type": "object", "additionalProperties": {"type": "string"}},
                "agent_versions": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        },
        "provenance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["engine_version", "engine_commit", "input_log_sha256"],
            "properties": {
                "engine_version": _NON_EMPTY,
                "engine_commit": {"type": "string"},
                "input_log_sha256": _HEX64,
            },
        },
        "reproducibility": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rerun_command"],
            "properties": {"rerun_command": _NON_EMPTY},
        },
        "integrity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["chain_index", "record_sha256", "prev_record_sha256"],
            "properties": {
                "chain_index": {"type": "integer", "minimum": 0},
                "record_sha256": _HEX64,
                "prev_record_sha256": _HEX64,
            },
        },
        "retention": {
            "type": "object",
            "additionalProperties": False,
            "required": ["class", "min_retention_months", "note"],
            "properties": {
                "class": {"const": "ai_act_art_26_6"},
                "min_retention_months": {"const": 6},
                "note": {
                    "const": (
                        "financial institutions: retain per applicable Union financial services law"
                    )
                },
            },
        },
    },
}


@pytest.fixture(scope="session")
def ruleset() -> RuleSet:
    return load_ruleset(RULES)


@pytest.fixture(scope="session")
def crosswalk(ruleset: RuleSet) -> Crosswalk:
    return load_crosswalk(
        FIXTURE_CROSSWALK, required_ids={rule.constraint_id for rule in ruleset.rules}
    )


@pytest.fixture(scope="session")
def evidence_base_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.agents import run_fleet

    out = tmp_path_factory.mktemp("evidence_base") / "spans"
    run_fleet(16, 7, out, provider="scripted", seed_data=FIXTURE_SAMPLE, quiet=True)
    return out


@pytest.fixture(scope="session")
def evidence_splits(evidence_base_run: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from auditors_trace.scenario.injector import inject_run, load_catalogue

    out = tmp_path_factory.mktemp("evidence_splits")
    inject_run(evidence_base_run, out, 7, load_catalogue(CATALOGUE), SIZES)
    return out


def _map_run(split_dir: Path) -> MappedRun:
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_run

    return map_span_trees(build_span_trees(read_run(split_dir / "manifest.json")))


def _render_split(
    split_dir: Path, ruleset: RuleSet, crosswalk: Crosswalk
) -> tuple[list[EvidenceRecord], MappedRun]:
    run = _map_run(split_dir)
    records = render_all(
        evaluate(run.log, ruleset),
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=build_render_index(run.log, run.span_index),
        input_log_sha256=log_hash(run.log),
        crosswalk_sha256=sha256_bytes(FIXTURE_CROSSWALK.read_bytes()),
    )
    return records, run


def _jsonl(records: list[EvidenceRecord]) -> bytes:
    from auditors_trace.evidence.chain import canonical_json

    return "".join(
        canonical_json(record.model_dump(mode="json", by_alias=True, exclude_none=True)) + "\n"
        for record in records
    ).encode("utf-8")


@pytest.fixture(scope="session")
def single_records(
    evidence_splits: Path, ruleset: RuleSet, crosswalk: Crosswalk
) -> list[EvidenceRecord]:
    records, _ = _render_split(evidence_splits / "single", ruleset, crosswalk)
    return records


def test_record_matches_schema(single_records: list[EvidenceRecord]) -> None:
    """Every chained record conforms to the section 8 JSON Schema."""
    import jsonschema

    assert single_records
    for record in single_records:
        payload = json.loads(_jsonl([record]).decode("utf-8"))
        jsonschema.validate(payload, RECORD_SCHEMA)


def test_record_hash_is_stable_across_runs(
    evidence_splits: Path, ruleset: RuleSet, crosswalk: Crosswalk
) -> None:
    first, _ = _render_split(evidence_splits / "single", ruleset, crosswalk)
    second, _ = _render_split(evidence_splits / "single", ruleset, crosswalk)
    assert [r.integrity for r in first] == [r.integrity for r in second]
    assert _jsonl(first) == _jsonl(second)


def test_record_hash_excludes_generated_at(single_records: list[EvidenceRecord]) -> None:
    record = single_records[0]
    stamped = record.model_copy(update={"generated_at": "2026-08-19T12:00:00Z"})
    assert record_hash(stamped) == record_hash(record)


def test_chain_is_valid(single_records: list[EvidenceRecord]) -> None:
    """Each record's prev hash equals the previous record's hash (week-3 gate)."""
    assert verify_chain(single_records)
    assert single_records[0].integrity is not None
    assert single_records[0].integrity.prev_record_sha256 == GENESIS_HASH


def test_chain_detects_tampering(single_records: list[EvidenceRecord]) -> None:
    """Mutate one record, verification fails — the DoD's tamper test."""
    victim = single_records[1]
    tampered = victim.model_copy(
        update={"constraint": victim.constraint.model_copy(update={"ruleset_version": "fake"})}
    )
    forged = [*single_records[:1], tampered, *single_records[2:]]
    assert not verify_chain(forged)


def test_every_record_has_at_least_one_legal_basis(
    single_records: list[EvidenceRecord],
) -> None:
    for record in single_records:
        assert record.legal_basis, record.violation_id


def test_golden_evidence_bitwise_identical(single_records: list[EvidenceRecord]) -> None:
    """The B4 evidence-bundle check: byte-for-byte identical on Windows and
    Ubuntu via the CI matrix. Regenerate deliberately with
    tests/golden/evidence/_regen.py when the fixture crosswalk, the ruleset,
    or the engine version changes — never casually."""
    golden = GOLDEN / "hermetic_single.evidence.jsonl"
    assert golden.exists(), "cut the golden with tests/golden/evidence/_regen.py first"
    assert _jsonl(single_records) == golden.read_bytes()


def test_rerun_command_reproduces_record(
    evidence_splits: Path,
    single_records: list[EvidenceRecord],
    tmp_path: Path,
) -> None:
    """An auditor runs the record's own rerun command (artifact paths
    supplied at invocation) and gets the byte-identical evidence log back."""
    from auditors_trace.cli import main
    from auditors_trace.model.io import write_ocel

    run = _map_run(evidence_splits / "single")
    ocel_path = tmp_path / "single.jsonocel"
    write_ocel(run.log, ocel_path, "json")
    from auditors_trace.ingest.mapper import span_index_json

    index_path = tmp_path / "single.jsonocel.span_index.json"
    index_path.write_text(span_index_json(run.span_index), encoding="utf-8", newline="\n")

    command = single_records[0].reproducibility.rerun_command
    prefix = "python -m auditors_trace.cli "
    assert command.startswith(prefix)
    out_path = tmp_path / "reproduced.evidence.jsonl"
    argv = [
        *command.removeprefix(prefix).split(),
        "--ocel",
        str(ocel_path),
        "--span-index",
        str(index_path),
        "--rules-file",
        str(RULES),
        "--crosswalk",
        str(FIXTURE_CROSSWALK),
        "--out",
        str(out_path),
    ]
    assert main(argv) == 0
    assert out_path.read_bytes() == _jsonl(single_records)


def test_rendered_spans_match_ground_truth(
    evidence_splits: Path, single_records: list[EvidenceRecord]
) -> None:
    """B1's traceability requirement: every span a record cites is
    pre-registered as exhibiting the violation.

    Subset, not equality, by pre-registered design: the section 8 locator
    pairs spans 1:1 with the cited anchor EVENTS, while the injector's
    expected_evidence_span_ids also pre-register manifestation sites beyond
    the anchor (V3 includes the request_approval span; V6 variant B the
    emit_reasoning span whose explains edge was stripped). A record citing a
    span outside the pre-registered set would be the B1 failure."""
    from auditors_trace.scenario.injector import read_labels

    labels = read_labels(evidence_splits / "single" / "labels.json")
    by_key = {
        (record.constraint.id, frozenset(record.evidence.ocel_event_ids)): record
        for record in single_records
    }
    assert labels.violations
    for truth in labels.violations:
        record = by_key[(truth.constraint_id, frozenset(truth.ocel_event_ids))]
        cited = set(record.evidence.otel_span_ids)
        assert cited, truth.violation_id
        assert cited <= set(truth.expected_evidence_span_ids), truth.violation_id


def test_clean_split_renders_an_empty_valid_chain(
    evidence_splits: Path, ruleset: RuleSet, crosswalk: Crosswalk
) -> None:
    records, _ = _render_split(evidence_splits / "clean", ruleset, crosswalk)
    assert records == []
    assert verify_chain(records)


def test_hermetic_gt_is_a_bijection(
    evidence_splits: Path, single_records: list[EvidenceRecord]
) -> None:
    """Adversarial-review regression: the GT test was unidirectional — a
    spurious record matched nothing and was checked against nothing. The
    exact join is a bijection: same count, identical key sets."""
    from auditors_trace.scenario.injector import read_labels

    labels = read_labels(evidence_splits / "single" / "labels.json")
    truth_keys = {
        (truth.constraint_id, frozenset(truth.ocel_event_ids)) for truth in labels.violations
    }
    record_keys = {
        (record.constraint.id, frozenset(record.evidence.ocel_event_ids))
        for record in single_records
    }
    assert len(single_records) == len(labels.violations)
    assert record_keys == truth_keys


def test_span_citation_pairing_matches_the_sidecar(
    evidence_splits: Path, single_records: list[EvidenceRecord]
) -> None:
    """Adversarial-review regression: the subset check alone could not catch
    an anchor event paired with a wrong-but-preregistered span. Amendment 7's
    property is 1:1 pairing: each cited event's span id is that event's OWN
    span in the sidecar, in citation order."""
    run = _map_run(evidence_splits / "single")
    own_span = {ref.event_id: ref.span_id for ref in run.span_index.events}
    for record in single_records:
        expected = tuple(own_span[event_id] for event_id in record.evidence.ocel_event_ids)
        assert record.evidence.otel_span_ids == expected, record.violation_id


def test_mixed_split_renders_chains_and_matches_ground_truth(
    evidence_splits: Path, ruleset: RuleSet, crosswalk: Crosswalk
) -> None:
    """Adversarial-review regression: composed injections were never rendered
    by any test. Schema, chain, bijection, and span pairing on mixed."""
    import jsonschema

    from auditors_trace.scenario.injector import read_labels

    records, run = _render_split(evidence_splits / "mixed", ruleset, crosswalk)
    labels = read_labels(evidence_splits / "mixed" / "labels.json")
    assert records
    assert verify_chain(records)
    assert len(records) == len(labels.violations)
    truth_keys = {
        (truth.constraint_id, frozenset(truth.ocel_event_ids)) for truth in labels.violations
    }
    own_span = {ref.event_id: ref.span_id for ref in run.span_index.events}
    for record in records:
        jsonschema.validate(json.loads(_jsonl([record]).decode("utf-8")), RECORD_SCHEMA)
        assert (record.constraint.id, frozenset(record.evidence.ocel_event_ids)) in truth_keys
        expected = tuple(own_span[event_id] for event_id in record.evidence.ocel_event_ids)
        assert record.evidence.otel_span_ids == expected


# --- Full-scale verification against the committed corpus (local only) ------


@full_scale
def test_full_scale_single_split_renders_and_chains(ruleset: RuleSet, crosswalk: Crosswalk) -> None:
    from auditors_trace.scenario.injector import read_labels

    records, _run = _render_split(GENERATED_SPLITS / "single", ruleset, crosswalk)
    assert len(records) == 240
    assert verify_chain(records)

    labels = read_labels(GENERATED_SPLITS / "single" / "labels.json")
    by_key = {
        (record.constraint.id, frozenset(record.evidence.ocel_event_ids)): record
        for record in records
    }
    for truth in labels.violations:
        record = by_key[(truth.constraint_id, frozenset(truth.ocel_event_ids))]
        cited = set(record.evidence.otel_span_ids)
        assert cited, truth.violation_id
        assert cited <= set(truth.expected_evidence_span_ids), truth.violation_id
