"""End-to-end scenario tests over a real 10-session scripted run.

These tests read the span files back through ``model.span_contract``'s parsers
— exactly the code path Phase 3's reader will use — so every assertion here is
also a guarantee to the mapper.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import (
    EVENT_TYPE,
    EventSpec,
    parse_event,
    parse_scope,
)
from auditors_trace.scenario.__main__ import main
from auditors_trace.scenario.agents import run_fleet
from auditors_trace.scenario.catalogue import default_catalogue_path, load_catalogue

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"
SESSIONS = 10
# Seed 7's first ten draws cover all three outcomes, both approver roles, and
# exactly one human override — so all 13 event types (incl. deny_approval)
# genuinely appear and Phase 3 can be built against this run.
SEED = 7


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("spans")
    return run_fleet(SESSIONS, SEED, out, seed_data=FIXTURE, quiet=True)


@pytest.fixture(scope="module")
def manifest(run_dir: Path) -> dict[str, Any]:
    payload = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.fixture(scope="module")
def traces(run_dir: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Session id -> span rows, loaded via the manifest (never listdir)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for filename, _digest in manifest["files"]:
        rows = [
            json.loads(line)
            for line in (run_dir / filename).read_text(encoding="utf-8").splitlines()
        ]
        out[filename.removesuffix(".jsonl")] = rows
    return out


def _events(rows: list[dict[str, Any]]) -> list[EventSpec]:
    specs = []
    for row in rows:
        spec = parse_event(row["attributes"])
        if spec is not None:
            specs.append(spec)
    return sorted(specs, key=lambda s: s.seq)


def _is_layer_b_llm_or_tool(row: dict[str, Any]) -> bool:
    # All three layer-B vocabularies: a future genai-native instrumentor must
    # still satisfy the descent rule. gen_ai.conversation.id alone does not
    # make a span enrichment — the dual-write derives it from session.id on
    # orchestration chain spans (graph/node), which are structure, not payload.
    attrs = row["attributes"]
    if EVENT_TYPE in attrs:
        return False
    return any(
        key.startswith(("llm.", "tool."))
        or (key.startswith("gen_ai.") and key != "gen_ai.conversation.id")
        for key in attrs
    )


class TestScenarioRunsEndToEnd:
    def test_scenario_runs_end_to_end(
        self, run_dir: Path, manifest: dict[str, Any], traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        assert manifest["session_count"] == SESSIONS
        assert manifest["provider"] == "scripted"
        assert manifest["model_id"] == "scripted-credit-policy-v1"
        assert len(manifest["files"]) == SESSIONS
        for filename, digest in manifest["files"]:
            payload = (run_dir / filename).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == digest, filename
        for session_id, rows in traces.items():
            events = _events(rows)
            assert len(events) == 28, session_id
            assert [e.seq for e in events] == list(range(1, 29)), session_id

    def test_cli_run_path(self, tmp_path: Path) -> None:
        code = main(
            [
                "run",
                "--n",
                "1",
                "--seed",
                "7",
                "--out",
                str(tmp_path),
                "--seed-data",
                str(FIXTURE),
                "--quiet",
            ]
        )
        assert code == 0
        assert (tmp_path / "manifest.json").exists()

    def test_cli_usage_error(self) -> None:
        assert main(["run"]) == 2

    def test_scope_parses_on_every_layer_a_span(
        self, traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        for session_id, rows in traces.items():
            for row in rows:
                scope = parse_scope(row["attributes"])
                if scope is not None:
                    assert scope.session_id == session_id
                    assert scope.run_seed == SEED


class TestSpanTreeIsWellFormed:
    def test_span_tree_is_well_formed(self, traces: dict[str, list[dict[str, Any]]]) -> None:
        for session_id, rows in traces.items():
            by_id = {row["span_id"]: row for row in rows}
            assert len(by_id) == len(rows), f"{session_id}: duplicate span ids"
            trace_ids = {row["trace_id"] for row in rows}
            assert len(trace_ids) == 1, f"{session_id}: multiple traces in one file"
            roots = [row for row in rows if row["parent_span_id"] is None]
            assert len(roots) == 1, f"{session_id}: expected exactly one root"
            assert roots[0]["attributes"].get("at.span.role") == "session_root"
            for row in rows:
                parent_id = row["parent_span_id"]
                if parent_id is not None:
                    assert parent_id in by_id, f"{session_id}: unresolvable parent"
            # acyclic and rooted
            for row in rows:
                seen: set[str] = set()
                node = row
                while node["parent_span_id"] is not None:
                    assert node["span_id"] not in seen, f"{session_id}: cycle"
                    seen.add(node["span_id"])
                    node = by_id[node["parent_span_id"]]
                assert node is roots[0] or node["parent_span_id"] is None

    def test_every_llm_or_tool_span_descends_from_an_event(
        self, traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        # The layering rule the whole Phase 3 enrichment model rests on.
        for session_id, rows in traces.items():
            by_id = {row["span_id"]: row for row in rows}
            for row in rows:
                if not _is_layer_b_llm_or_tool(row):
                    continue
                node = row
                found = False
                while node["parent_span_id"] is not None:
                    node = by_id[node["parent_span_id"]]
                    if EVENT_TYPE in node["attributes"]:
                        found = True
                        break
                assert found, f"{session_id}: {row['name']} has no event ancestor"

    def test_layers_never_mix_vocabularies(self, traces: dict[str, list[dict[str, Any]]]) -> None:
        for rows in traces.values():
            for row in rows:
                attrs = row["attributes"]
                has_at = any(k.startswith("at.") for k in attrs)
                has_llm_tool = any(k.startswith(("llm.", "tool.", "gen_ai.")) for k in attrs)
                assert not (has_at and has_llm_tool), row["name"]


class TestGenAiAttributes:
    def test_spans_contain_required_genai_attributes(
        self, traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        for session_id, rows in traces.items():
            keys = {key for row in rows for key in row["attributes"]}
            assert "gen_ai.request.model" in keys, session_id
            assert "gen_ai.tool.name" in keys, session_id
            # and the OpenInference equivalents on the same spans
            assert "llm.model_name" in keys, session_id
            assert "tool.name" in keys, session_id
            for row in rows:
                if _is_layer_b_llm_or_tool(row):
                    attrs = row["attributes"]
                    assert attrs.get("session.id") == session_id
                    assert attrs.get("gen_ai.conversation.id") == session_id

    def test_genai_semconv_off_leaves_openinference_only(self, tmp_path: Path) -> None:
        # The fixture Phase 3's test_both_vocabularies_map_to_same_ocel consumes.
        run_fleet(1, SEED, tmp_path, seed_data=FIXTURE, genai_semconv=False, quiet=True)
        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["genai_semconv"] is False
        rows = [
            json.loads(line)
            for filename, _ in manifest["files"]
            for line in (tmp_path / filename).read_text(encoding="utf-8").splitlines()
        ]
        keys = {key for row in rows for key in row["attributes"]}
        assert not any(key.startswith("gen_ai.") for key in keys)
        assert "llm.model_name" in keys
        assert "tool.name" in keys
        assert "session.id" in keys


class TestContractCoverage:
    def test_all_object_and_event_types_appear(
        self, traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        # If any type never appears, Phase 3's mapper cannot be written for it.
        event_types: set[EventType] = set()
        object_types: set[ObjectType] = set()
        qualifiers: set[Qualifier] = set()
        for rows in traces.values():
            for event in _events(rows):
                event_types.add(event.event_type)
                for ref in event.objects:
                    object_types.add(ref.object_type)
                    if ref.qualifier is not None:
                        qualifiers.add(ref.qualifier)
                for rel in event.o2o:
                    qualifiers.add(rel.qualifier)
        assert event_types == set(EventType)
        assert object_types == set(ObjectType)
        assert qualifiers == set(Qualifier)

    def test_every_referenced_object_is_declared_exactly_once(
        self, traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        expected_placement = {
            ObjectType.SESSION: EventType.SESSION_START,
            ObjectType.APPLICATION: EventType.SESSION_START,
            ObjectType.APPLICANT: EventType.SESSION_START,
            ObjectType.POLICY_VERSION: EventType.SESSION_START,
            ObjectType.AGENT: EventType.INVOKE_AGENT,
            ObjectType.MODEL: EventType.CALL_LLM,
            ObjectType.PROMPT: EventType.CALL_LLM,
            ObjectType.TOOL: EventType.CALL_TOOL,
            ObjectType.DATA_RESOURCE: EventType.RETRIEVE_DATA,
            ObjectType.HUMAN_APPROVER: EventType.REQUEST_APPROVAL,
        }
        for session_id, rows in traces.items():
            declared: dict[str, tuple[ObjectType, EventType]] = {}
            referenced: set[str] = set()
            for event in _events(rows):
                for ref in event.objects:
                    referenced.add(ref.object_id)
                    if ref.declares:
                        assert ref.object_id not in declared, (
                            f"{session_id}: {ref.object_id} declared twice"
                        )
                        declared[ref.object_id] = (ref.object_type, event.event_type)
                        assert ref.attributes, f"{session_id}: empty declaration"
                for rel in event.o2o:
                    referenced.update((rel.source_id, rel.target_id))
            undeclared = referenced - set(declared)
            assert not undeclared, f"{session_id}: referenced but undeclared {undeclared}"
            for object_id, (object_type, declared_at) in declared.items():
                if object_type in expected_placement:
                    assert declared_at == expected_placement[object_type], (
                        f"{session_id}: {object_id} declared on {declared_at}"
                    )
                elif object_type is ObjectType.APPROVAL:
                    assert declared_at in (
                        EventType.GRANT_APPROVAL,
                        EventType.DENY_APPROVAL,
                    )
                elif object_type is ObjectType.CREDIT_DECISION:
                    assert declared_at == EventType.MAKE_DECISION

    def test_no_span_attributes_were_dropped(self, traces: dict[str, list[dict[str, Any]]]) -> None:
        # Guards the silent 128-attribute SpanLimits truncation.
        for rows in traces.values():
            for row in rows:
                assert row["dropped"] == {"attributes": 0, "events": 0, "links": 0}, row["name"]

    def test_llm_calls_record_temperature_and_seed(
        self, traces: dict[str, list[dict[str, Any]]]
    ) -> None:
        catalogue = load_catalogue(default_catalogue_path())
        for session_id, rows in traces.items():
            call_llm_events = [e for e in _events(rows) if e.event_type is EventType.CALL_LLM]
            assert len(call_llm_events) == 3, session_id
            for event in call_llm_events:
                attrs = dict(event.attributes)
                assert attrs["temperature"] == 0.0
                assert attrs["model_seed"] == catalogue.model.seed
            # ... and the layer-B invocation parameters carry them too, but
            # never the script text.
            for row in rows:
                params = row["attributes"].get("llm.invocation_parameters")
                if params is not None:
                    parsed = json.loads(params)
                    assert parsed["temperature"] == 0.0
                    assert parsed["seed"] == catalogue.model.seed
                    assert "script" not in parsed
                    assert "notice" not in params  # fragment of the script text


@pytest.fixture(scope="module")
def all_events(traces: dict[str, list[dict[str, Any]]]) -> dict[str, list[EventSpec]]:
    return {sid: _events(rows) for sid, rows in traces.items()}


class TestRawMaterialForCatalogueViolations:
    """One assertion per planned injector: the clean log must contain what
    each of V1-V8 mutates. This is the test that stops Phase 4 discovering,
    a week later, that the scenario cannot host half the catalogue."""

    def test_v1_every_session_has_exactly_one_approval_event(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        for session_id, events in all_events.items():
            approvals = [
                e
                for e in events
                if e.event_type in (EventType.GRANT_APPROVAL, EventType.DENY_APPROVAL)
            ]
            assert len(approvals) == 1, session_id

    def test_v2_approvals_cite_a_decision_and_ids_differ_across_sessions(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        decision_ids = set()
        for session_id, events in all_events.items():
            approval = next(
                e
                for e in events
                if e.event_type in (EventType.GRANT_APPROVAL, EventType.DENY_APPROVAL)
            )
            # Section 5 reserves `approves` for grant_approval; a denial cites
            # the decision with `concerns` instead.
            expected = (
                Qualifier.APPROVES
                if approval.event_type is EventType.GRANT_APPROVAL
                else Qualifier.CONCERNS
            )
            cited = [
                r
                for r in approval.objects
                if r.object_type is ObjectType.CREDIT_DECISION and r.qualifier is expected
            ]
            assert len(cited) == 1, session_id
            if approval.event_type is EventType.DENY_APPROVAL:
                assert not any(r.qualifier is Qualifier.APPROVES for r in approval.objects), (
                    f"{session_id}: a denial must not carry an approves edge"
                )
            decision_ids.add(cited[0].object_id)
        assert len(decision_ids) >= 2  # V2 repoints one approval at another decision

    def test_v3_multiple_authorised_roles_are_observed(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        catalogue = load_catalogue(default_catalogue_path())
        allowed = {a.role for a in catalogue.approvers}
        observed = set()
        for events in all_events.values():
            for event in events:
                if event.event_type in (EventType.GRANT_APPROVAL, EventType.DENY_APPROVAL):
                    observed.add(dict(event.attributes)["approver_role"])
        assert observed <= allowed
        assert len(observed) >= 2  # amount-based routing produced real variety

    def test_v4_every_required_source_is_retrieved_each_session(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        catalogue = load_catalogue(default_catalogue_path())
        for session_id, events in all_events.items():
            retrieved = {
                dict(e.attributes)["resource_type"]
                for e in events
                if e.event_type is EventType.RETRIEVE_DATA
            }
            assert set(catalogue.required_source_types) <= retrieved, session_id

    def test_v5_special_category_resource_has_a_lawful_basis_to_blank(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        for session_id, events in all_events.items():
            special = [
                ref
                for e in events
                for ref in e.objects
                if ref.declares
                and ref.object_type is ObjectType.DATA_RESOURCE
                and dict(ref.attributes).get("classification") == "special_category"
            ]
            assert len(special) == 1, session_id
            basis = dict(special[0].attributes)["lawful_basis"]
            assert isinstance(basis, str), session_id
            # Corrected legal anchor (docs/PLAN-REVIEW.md A5): bias examination
            # under Art. 10(2)(f)-(g); 10(5)-analog documentation is voluntary.
            assert "10(2)(f)-(g)" in basis, session_id

    def test_v6_a_deny_decision_with_reason_codes_exists(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        denies = []
        for events in all_events.values():
            for event in events:
                if event.event_type is EventType.MAKE_DECISION:
                    attrs = dict(event.attributes)
                    if attrs["outcome"] == "deny":
                        denies.append(attrs)
        assert denies, "no deny outcome in the run; V6 has nothing to blank"
        for attrs in denies:
            codes = attrs["reason_codes"]
            assert isinstance(codes, list | tuple)
            assert len(codes) >= 1

    def test_v7_exactly_three_handoffs_with_from_and_to(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        for session_id, events in all_events.items():
            handoffs = [e for e in events if e.event_type is EventType.HANDOFF]
            assert len(handoffs) == 3, session_id
            for event in handoffs:
                quals = {r.qualifier for r in event.objects}
                assert Qualifier.FROM in quals
                assert Qualifier.TO in quals
                assert any(rel.qualifier is Qualifier.DELEGATES_TO for rel in event.o2o)

    def test_v8_a_superseded_policy_is_declared_but_never_related(
        self, all_events: dict[str, list[EventSpec]]
    ) -> None:
        for session_id, events in all_events.items():
            declared_policies = [
                ref
                for e in events
                for ref in e.objects
                if ref.declares and ref.object_type is ObjectType.POLICY_VERSION
            ]
            assert len(declared_policies) == 2, session_id
            superseded = [
                ref for ref in declared_policies if dict(ref.attributes)["effective_to"] != ""
            ]
            assert len(superseded) == 1, session_id
            assert superseded[0].qualifier is None, (
                f"{session_id}: the superseded policy must be a pure declaration"
            )

    def test_t3_session_bounds_carry_no_actor(self, all_events: dict[str, list[EventSpec]]) -> None:
        # T3's clean-split safety: session_start/session_end must not look
        # like agent actions.
        for events in all_events.values():
            for event in events:
                if event.event_type in (EventType.SESSION_START, EventType.SESSION_END):
                    assert event.actor_id is None
                    assert not any(r.qualifier is Qualifier.PERFORMED_BY for r in event.objects)


GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "spans"


class TestGoldenRegeneration:
    """The committed golden fixtures must be what the current generator
    produces — modulo exactly the two documented volatile fields. Without
    this, a contract-valid writer change could silently strand Phase 3's
    byte-exact goldens on output the generator can no longer produce."""

    @pytest.mark.parametrize(
        ("golden_name", "seed"),
        [
            ("credit_grant_approval_seed42.jsonl", 42),
            ("credit_deny_approval_refer_seed2.jsonl", 2),
        ],
    )
    def test_goldens_match_the_current_generator(
        self,
        golden_name: str,
        seed: int,
        tmp_path: Path,
        span_line_normaliser: Any,
    ) -> None:
        run_fleet(1, seed, tmp_path, seed_data=FIXTURE, quiet=True)
        produced = (tmp_path / "SESS-0000.jsonl").read_text(encoding="utf-8").splitlines()
        committed = (GOLDEN / golden_name).read_text(encoding="utf-8").splitlines()
        assert len(produced) == len(committed), golden_name
        for i, (new, old) in enumerate(zip(produced, committed, strict=True)):
            assert span_line_normaliser(new) == span_line_normaliser(old), (
                f"{golden_name} line {i}: the generator no longer reproduces the "
                "committed golden — regenerate it deliberately or revert the change"
            )
