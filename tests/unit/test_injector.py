"""Phase 4: the violation catalogue loader and the span-surgery toolkit.

The catalogue is the pre-registered ground truth (invariant I4); its loader
enforces CLAUDE.md hard rule 4 (every entry carries legal article
references) and the B11 composition-matrix symmetry. The surgery helpers
are the only way the injectors touch span attributes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.scenario.injector import (
    REGISTRY,
    Catalogue,
    CatalogueEntry,
    catalogue_hash,
    load_catalogue,
)

REPO = Path(__file__).resolve().parent.parent.parent
CATALOGUE = REPO / "data" / "catalogue" / "violations.yaml"
SPANS = Path(__file__).resolve().parent.parent / "golden" / "spans"
GRANT = SPANS / "credit_grant_approval_seed42.jsonl"
DENY = SPANS / "credit_deny_approval_refer_seed2.jsonl"


def test_each_catalogue_entry_has_article_reference() -> None:
    catalogue = load_catalogue(CATALOGUE)
    assert len(catalogue.entries) == 8
    for entry in catalogue.entries:
        assert entry.articles, entry.entry_id
        for article in entry.articles:
            assert "Art." in article, (entry.entry_id, article)


class TestCatalogueLoader:
    def test_loads_the_draft(self) -> None:
        catalogue = load_catalogue(CATALOGUE)
        assert isinstance(catalogue, Catalogue)
        assert catalogue.schema_version == 1
        assert catalogue.scenario_name == "credit_scoring"
        assert [e.entry_id for e in catalogue.entries] == [f"V{i}" for i in range(1, 9)]
        assert catalogue.sha256 == catalogue_hash(CATALOGUE)
        by_id = {e.entry_id: e for e in catalogue.entries}
        assert by_id["V1"].constraint_id == "T1.synchronised_approval"
        assert by_id["V8"].constraint_id == "STD.policy_version_current"
        assert isinstance(by_id["V3"], CatalogueEntry)

    def test_every_injection_function_resolves_in_the_registry(self) -> None:
        catalogue = load_catalogue(CATALOGUE)
        for entry in catalogue.entries:
            assert entry.injection_function in REGISTRY, entry.entry_id

    def test_composition_matrix_is_symmetric_and_t1_exclusive(self) -> None:
        catalogue = load_catalogue(CATALOGUE)
        by_id = {e.entry_id: e for e in catalogue.entries}
        for entry in catalogue.entries:
            for other in entry.co_injectable_with:
                assert entry.entry_id in by_id[other].co_injectable_with, (
                    entry.entry_id,
                    other,
                )
        # V1/V2/V3 all violate T1 on the same decision: pairwise excluded.
        for a in ("V1", "V2", "V3"):
            for b in ("V1", "V2", "V3"):
                assert b not in by_id[a].co_injectable_with

    def _write(self, tmp_path: Path, mutate: Any) -> Path:
        import yaml

        doc = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
        mutate(doc)
        out = tmp_path / "violations.yaml"
        out.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return out

    def test_empty_articles_rejected(self, tmp_path: Path) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["violations"][2]["articles"] = []

        with pytest.raises(ValueError, match="article"):
            load_catalogue(self._write(tmp_path, mutate))

    def test_unknown_entry_key_rejected(self, tmp_path: Path) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["violations"][0]["frobnication"] = True

        with pytest.raises(ValueError, match="frobnication"):
            load_catalogue(self._write(tmp_path, mutate))

    def test_unresolvable_injection_function_rejected(self, tmp_path: Path) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["violations"][0]["injection_function"] = "fault_v99"

        with pytest.raises(ValueError, match="fault_v99"):
            load_catalogue(self._write(tmp_path, mutate))

    def test_asymmetric_matrix_rejected(self, tmp_path: Path) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["violations"][0]["co_injectable_with"] = ["V2"]  # V2 excludes V1

        with pytest.raises(ValueError, match="symmetr"):
            load_catalogue(self._write(tmp_path, mutate))

    def test_duplicate_entry_id_rejected(self, tmp_path: Path) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["violations"][1]["id"] = "V1"

        with pytest.raises(ValueError, match="duplicate"):
            load_catalogue(self._write(tmp_path, mutate))

    def test_catalogue_hash_is_over_exact_bytes(self, tmp_path: Path) -> None:
        import hashlib

        assert catalogue_hash(CATALOGUE) == hashlib.sha256(CATALOGUE.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Shared machinery for surgery/fault tests
# ---------------------------------------------------------------------------


def _rows(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)


def _write_rows(tmp_path: Path, name: str, rows: tuple[dict[str, Any], ...]) -> Path:
    out = tmp_path / name
    out.write_text(
        "".join(
            json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            for r in rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    return out


def _second_session(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """The grant golden re-identified as SESS-0001 on a different trace,
    with a distinct decision id (session-unique ids may differ freely)."""
    text = "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows)
    text = (
        text.replace("SESS-0000", "SESS-0001")
        .replace("e2f2b80b7d3bb82ca7cdcbb612e36a77", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        .replace("DEC-APP-bc438fbf25", "DEC-APP-ffffffff01")
    )
    return tuple(json.loads(line) for line in text.splitlines())


def _map_rows(tmp_path: Path, *sessions: tuple[dict[str, Any], ...]) -> Any:
    from auditors_trace.ingest.mapper import map_span_trees
    from auditors_trace.ingest.otel_reader import build_span_trees, read_spans

    spans: list[Any] = []
    for index, rows in enumerate(sessions):
        spans.extend(read_spans(_write_rows(tmp_path, f"s{index}.jsonl", rows)))
    ordered = tuple(sorted(spans, key=lambda s: (s.trace_id, s.creation_index)))
    return map_span_trees(build_span_trees(ordered))


def _inject_one(
    rows: tuple[dict[str, Any], ...],
    entry_id: str,
    *,
    seed: int = 7,
    donor_decision_ids: tuple[str, ...] = (),
) -> Any:
    """Run one registered fault on one session, with renumber + recompute."""
    from auditors_trace.scenario.injector import (
        FaultContext,
        RunView,
        _recompute_session_end,
        _renumber_seq,
        _session_view,
    )

    catalogue = load_catalogue(CATALOGUE)
    entry = next(e for e in catalogue.entries if e.entry_id == entry_id)
    view = _session_view(rows)
    ctx = FaultContext(
        seed=seed,
        entry=entry,
        session=view,
        run=RunView(sessions=(view,)),
        variant_key=f"vk-{seed}-{entry_id}-{view.session_id}",
        donor_decision_ids=donor_decision_ids,
    )
    result = REGISTRY[entry.injection_function].fn(rows, ctx)
    final_rows = _recompute_session_end(_renumber_seq(result.rows))
    return final_rows, result.draft


class TestManifestDriftGuard:
    def test_split_manifest_matches_the_telemetry_writer_byte_for_byte(
        self, tmp_path: Path
    ) -> None:
        """The injector mirrors scenario/telemetry.write_manifest (which it
        may not import — the scenario extra). If the real writer's format
        ever moves, this test turns red before the mirror silently drifts."""
        pytest.importorskip("opentelemetry")
        from auditors_trace.scenario.injector import _write_split_manifest
        from auditors_trace.scenario.telemetry import RunManifest, write_manifest

        fields = {
            "schema_version": 1,
            "contract": "at-span/1",
            "run_id": "driftguard0000ff",
            "scenario_name": "credit_scoring",
            "scenario_version": "1.0.0",
            "seed": 42,
            "session_count": 2,
            "catalogue_sha256": "aa" * 32,
            "seed_data_sha256": "bb" * 32,
            "model_id": "scripted-credit-policy-v1",
            "provider": "scripted",
            "genai_semconv": True,
        }
        files = [("SESS-0001.jsonl", "cc" * 32), ("SESS-0000.jsonl", "dd" * 32)]

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        write_manifest(real_dir, RunManifest(files=tuple(files), **fields))  # type: ignore[arg-type]

        mirror_dir = tmp_path / "mirror"
        mirror_dir.mkdir()
        _write_split_manifest(mirror_dir, {**fields, "files": []}, files)

        assert (mirror_dir / "manifest.json").read_bytes() == (
            real_dir / "manifest.json"
        ).read_bytes()


class TestSurgeryHelpers:
    def test_mutate_event_rebuilds_attribute_block(self) -> None:
        from dataclasses import replace as dc_replace

        from auditors_trace.scenario.injector import _mutate_event, _spec_of

        rows = _rows(GRANT)
        event_row = next(r for r in rows if r["attributes"].get("at.event.seq") == 25)
        before_keys = {k for k in event_row["attributes"] if not k.startswith("at.")}
        mutated = _mutate_event(event_row, lambda spec: dc_replace(spec, seq=99))
        after_keys = {k for k in mutated["attributes"] if not k.startswith("at.")}
        assert before_keys == after_keys  # scope/session.id/span-kind survive
        assert mutated["attributes"]["at.event.seq"] == 99
        assert mutated["span_id"] == event_row["span_id"]
        assert mutated["creation_index"] == event_row["creation_index"]
        assert mutated["start_time_unix_nano"] == event_row["start_time_unix_nano"]
        spec = _spec_of(mutated)
        assert spec is not None
        assert spec.seq == 99

    def test_renumber_is_dense_and_event_ids_stable(self, tmp_path: Path) -> None:
        from auditors_trace.scenario.injector import _delete_spans, _renumber_seq, _spec_of

        rows = _rows(GRANT)
        victim = next(r for r in rows if r["attributes"].get("at.event.seq") == 14)
        remaining = _renumber_seq(_delete_spans(rows, frozenset({victim["span_id"]})))
        specs = sorted(
            (s for s in (_spec_of(r) for r in remaining) if s is not None),
            key=lambda s: s.seq,
        )
        assert [s.seq for s in specs] == list(range(1, 28))
        # Event ids keep the PRE-injection numeric infix by design.
        assert any("-015-" in s.event_id and s.seq == 14 for s in specs)

    def test_session_end_event_count_recomputed_duration_untouched(self) -> None:
        from auditors_trace.scenario.injector import (
            _delete_spans,
            _recompute_session_end,
            _renumber_seq,
            _spec_of,
        )

        rows = _rows(GRANT)
        victim = next(r for r in rows if r["attributes"].get("at.event.seq") == 14)
        out = _recompute_session_end(
            _renumber_seq(_delete_spans(rows, frozenset({victim["span_id"]})))
        )
        end_spec = next(
            s
            for s in (_spec_of(r) for r in out)
            if s is not None and s.event_type.value == "session_end"
        )
        attrs = dict(end_spec.attributes)
        assert attrs["event_count"] == 27
        original_end = next(
            s
            for s in (_spec_of(r) for r in _rows(GRANT))
            if s is not None and s.event_type.value == "session_end"
        )
        assert attrs["duration_seconds"] == dict(original_end.attributes)["duration_seconds"]


class TestTrapRegressions:
    """Each forbidden surgery fails; the injector's chosen surgery maps."""

    def test_v5_blanking_shared_resource_is_unmappable(self, tmp_path: Path) -> None:
        from auditors_trace.ingest.otel_reader import IngestError

        rows = list(_rows(GRANT))
        for i, row in enumerate(rows):
            attrs = dict(row["attributes"])
            for key, value in list(attrs.items()):
                if key.endswith(".attr.lawful_basis") and "10(2)(f)-(g)" in str(value):
                    attrs[key] = ""
                    rows[i] = {**row, "attributes": attrs}
        with pytest.raises(IngestError, match="redeclar"):
            _map_rows(tmp_path, tuple(rows), _second_session(_rows(GRANT)))

    def test_v5_session_local_resource_maps(self, tmp_path: Path) -> None:
        faulted, draft = _inject_one(_rows(GRANT), "V5")
        run = _map_rows(tmp_path, faulted, _second_session(_rows(GRANT)))
        local = [o for o in run.log.objects if o.object_id.startswith("RES-DEMOGRAPHICS-SESS")]
        assert len(local) == 1
        assert dict(local[0].attributes)["lawful_basis"] == ""
        assert draft.ocel_object_ids == (local[0].object_id,)

    def test_v3_mutating_shared_approver_is_unmappable(self, tmp_path: Path) -> None:
        from auditors_trace.ingest.otel_reader import IngestError

        rows = list(_rows(GRANT))
        for i, row in enumerate(rows):
            attrs = dict(row["attributes"])
            changed = False
            for key, value in list(attrs.items()):
                if (
                    "at.obj." in key
                    and key.endswith(".attr.role")
                    and value
                    in (
                        "credit_officer",
                        "senior_underwriter",
                    )
                ):
                    attrs[key] = "intern"
                    changed = True
            if changed:
                rows[i] = {**row, "attributes": attrs}
        with pytest.raises(IngestError, match="redeclar"):
            _map_rows(tmp_path, tuple(rows), _second_session(_rows(GRANT)))

    def test_v3_out_of_roster_approver_maps(self, tmp_path: Path) -> None:
        faulted, draft = _inject_one(_rows(GRANT), "V3")
        run = _map_rows(tmp_path, faulted)
        rogues = [o for o in run.log.objects if o.object_id.startswith("APR-9")]
        assert len(rogues) == 1
        attrs = dict(rogues[0].attributes)
        assert attrs["role"] in ("intern", "contractor", "trainee_analyst")
        assert attrs["authority_level"] == 0
        assert draft.ocel_object_ids == (rogues[0].object_id,)

    def test_v6_blanking_event_attr_is_rejected_by_the_contract(self) -> None:
        from dataclasses import replace as dc_replace

        from auditors_trace.model.span_contract import SpanContractError
        from auditors_trace.scenario.injector import _mutate_event

        rows = _rows(DENY)
        decision = next(r for r in rows if r["attributes"].get("at.event.type") == "make_decision")
        with pytest.raises(SpanContractError, match="reason_codes"):
            _mutate_event(
                decision,
                lambda spec: dc_replace(
                    spec,
                    attributes=tuple(
                        (n, () if n == "reason_codes" else v) for n, v in spec.attributes
                    ),
                ),
            )

    def test_v6_object_attr_blank_maps(self, tmp_path: Path) -> None:
        # Force variant A by trying seeds until the variant picker chooses it.
        from auditors_trace.model.ocel_schema import ObjectType

        for seed in range(20):
            faulted, draft = _inject_one(_rows(DENY), "V6", seed=seed)
            if "blank_object_reason_codes" in draft.description:
                break
        else:
            pytest.fail("variant picker never chose blank_object_reason_codes")
        run = _map_rows(tmp_path, faulted)
        decision = next(o for o in run.log.objects if o.object_type is ObjectType.CREDIT_DECISION)
        assert dict(decision.attributes)["reason_codes"] == ()

    def test_v4_deleting_call_tool_alone_orphans_its_child(self, tmp_path: Path) -> None:
        from auditors_trace.ingest.otel_reader import IngestError, build_span_trees, read_spans

        rows = _rows(GRANT)
        tool_row = next(r for r in rows if r["attributes"].get("at.event.type") == "call_tool")
        remaining = tuple(r for r in rows if r["span_id"] != tool_row["span_id"])
        with pytest.raises(IngestError, match=r"parent|orphan"):
            build_span_trees(read_spans(_write_rows(tmp_path, "orphan.jsonl", remaining)))

    def test_v4_both_delete_modes_map(self, tmp_path: Path) -> None:
        seen_modes: set[str] = set()
        for seed in range(20):
            faulted, draft = _inject_one(_rows(GRANT), "V4", seed=seed)
            mode = "tool_call" if len(draft.removed_span_ids) > 1 else "leaf"
            if mode in seen_modes:
                continue
            seen_modes.add(mode)
            sub = tmp_path / f"m{seed}"
            sub.mkdir()
            run = _map_rows(sub, faulted)
            expected = 27 if mode == "leaf" else 26
            assert len(run.log.events) == expected
            if seen_modes == {"leaf", "tool_call"}:
                break
        assert seen_modes == {"leaf", "tool_call"}

    def test_v2_fabricated_decision_id_is_unmappable(self, tmp_path: Path) -> None:
        from dataclasses import replace as dc_replace

        from auditors_trace.ingest.otel_reader import IngestError
        from auditors_trace.model.ocel_schema import Qualifier
        from auditors_trace.scenario.injector import _mutate_event

        rows = list(_rows(GRANT))
        for i, row in enumerate(rows):
            if row["attributes"].get("at.event.type") == "grant_approval":
                rows[i] = _mutate_event(
                    row,
                    lambda spec: dc_replace(
                        spec,
                        objects=tuple(
                            dc_replace(ref, object_id="DEC-APP-deadbeef00")
                            if ref.qualifier is Qualifier.APPROVES
                            else ref
                            for ref in spec.objects
                        ),
                    ),
                )
        with pytest.raises(IngestError, match="never declared"):
            _map_rows(tmp_path, tuple(rows))

    def test_v2_cross_session_repoint_maps(self, tmp_path: Path) -> None:
        second = _second_session(_rows(GRANT))
        from auditors_trace.scenario.injector import _session_view

        donor_view = _session_view(second)
        faulted, draft = _inject_one(
            _rows(GRANT), "V2", donor_decision_ids=(donor_view.decision_id,)
        )
        run = _map_rows(tmp_path, faulted, second)
        assert donor_view.decision_id in draft.ocel_object_ids
        approvals = [
            (e, r) for e in run.log.events for r in e.relations if r.qualifier.value == "approves"
        ]
        # Both sessions' approvals point at the SAME donor decision now.
        assert {r.object_id for _, r in approvals} == {donor_view.decision_id}


class TestRemainingFaultsMap:
    def test_v1_maps_with_26_events(self, tmp_path: Path) -> None:
        faulted, draft = _inject_one(_rows(GRANT), "V1")
        run = _map_rows(tmp_path, faulted)
        assert len(run.log.events) == 26
        assert len(draft.removed_span_ids) == 2

    def test_v7_both_modes_map(self, tmp_path: Path) -> None:
        seen: set[str] = set()
        for seed in range(30):
            faulted, draft = _inject_one(_rows(GRANT), "V7", seed=seed)
            mode = "delete" if draft.removed_span_ids else "repoint_to"
            if mode in seen:
                continue
            seen.add(mode)
            sub = tmp_path / f"v7{seed}"
            sub.mkdir()
            run = _map_rows(sub, faulted)
            assert len(run.log.events) == (27 if mode == "delete" else 28)
            if seen == {"delete", "repoint_to"}:
                break
        assert seen == {"delete", "repoint_to"}

    def test_v8_repoints_to_superseded_policy(self, tmp_path: Path) -> None:
        from auditors_trace.model.ocel_schema import Qualifier

        faulted, draft = _inject_one(_rows(GRANT), "V8")
        run = _map_rows(tmp_path, faulted)
        governed = [
            r.object_id
            for e in run.log.events
            for r in e.relations
            if r.qualifier is Qualifier.GOVERNED_BY and e.event_type.value == "make_decision"
        ]
        assert governed == ["POL-CREDIT-2025.11"]
        assert draft.ocel_object_ids == ("POL-CREDIT-2025.11",)
