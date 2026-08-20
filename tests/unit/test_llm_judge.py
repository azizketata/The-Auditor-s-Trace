"""LLM-as-judge harness (Phase 7): parsing, caching, variance, the lock.

Everything here is hermetic — the scripted client is deterministic and
content-derived, so cache/replay/aggregation tests run in CI with no key and
no network. Live calls are exercised only by the network-marked smoke test in
tests/integration/test_judge_dev_split.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditors_trace.baselines.llm_judge import (
    EVALUATION_BASE_RUN_ID,
    EvaluationLockError,
    JudgeError,
    JudgePrompts,
    JudgeRequest,
    JudgeRun,
    ScriptedJudgeClient,
    cache_key,
    judge,
    judge_all,
    load_judge_prompts,
    majority_vote,
    modal_verdict,
    prompt_sha256,
    render_prompt,
    render_rules_block,
    session_excerpt,
)
from auditors_trace.constraints.ruleset import RuleSet, load_ruleset
from auditors_trace.constraints.templates import Violation
from auditors_trace.eval.metrics import determinism_score, load_judge_matching
from auditors_trace.model.log import OCELLog

REPO = Path(__file__).resolve().parent.parent.parent
PROMPTS = REPO / "rules" / "judge_prompts.yaml"
RULES = REPO / "rules" / "rules.yaml"
MATCHING = REPO / "rules" / "judge_matching.yaml"


@pytest.fixture(scope="module")
def prompts() -> JudgePrompts:
    return load_judge_prompts(PROMPTS)


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    return load_ruleset(RULES)


def _judge_kwargs(
    prompts: JudgePrompts, ruleset: RuleSet, cache_dir: Path, **overrides: object
) -> dict[str, object]:
    base: dict[str, object] = {
        "model_id": "claude-haiku-4-5",
        "sample_index": 0,
        "condition": "ocel",
        "session_id": "SES-A",
        "payload": '{"session": "SES-A", "events": []}',
        "candidate_event_ids": ("EV-A-09", "EV-A-08"),
        "prompts": prompts,
        "ruleset": ruleset,
        "cache_dir": cache_dir,
        "client": ScriptedJudgeClient(),
    }
    base.update(overrides)
    return base


class TestMandated:
    def test_llm_judge_output_parses_or_reports_error(
        self, prompts: JudgePrompts, ruleset: RuleSet, tmp_path: Path
    ) -> None:
        """Valid scripted output parses; malformed output is reported as a
        schema failure with the raw text kept — never repaired."""
        run = judge(**_judge_kwargs(prompts, ruleset, tmp_path))  # type: ignore[arg-type]
        assert not run.schema_failure
        for violation in run.violations:
            assert violation.ocel_event_ids
            assert violation.session_id == "SES-A"

        def malformed(request: JudgeRequest) -> str:
            return "I think the session violates T1 {not json"

        broken = judge(
            **_judge_kwargs(
                prompts,
                ruleset,
                tmp_path,
                sample_index=1,
                client=ScriptedJudgeClient(malformed),
            )
        )  # type: ignore[arg-type]
        assert broken.schema_failure
        assert broken.violations == ()
        record = json.loads((tmp_path / f"{broken.cache_key}.json").read_text(encoding="utf-8"))
        assert record["response"]["raw_text"].startswith("I think")
        assert record["response"]["parsed"] is None

    def test_llm_judge_is_cached_and_replayable(
        self, prompts: JudgePrompts, ruleset: RuleSet, tmp_path: Path
    ) -> None:
        """First call writes the cache; a client-less replay reproduces the
        identical JudgeRun and leaves the cache bytes untouched."""
        first = judge(**_judge_kwargs(prompts, ruleset, tmp_path))  # type: ignore[arg-type]
        assert not first.from_cache
        cache_file = tmp_path / f"{first.cache_key}.json"
        before = cache_file.read_bytes()
        assert b"\r" not in before

        replayed = judge(**_judge_kwargs(prompts, ruleset, tmp_path, client=None))  # type: ignore[arg-type]
        assert replayed.from_cache
        assert replayed == JudgeRun(
            model_id=first.model_id,
            sample_index=first.sample_index,
            condition=first.condition,
            session_id=first.session_id,
            violations=first.violations,
            schema_failure=first.schema_failure,
            cache_key=first.cache_key,
            from_cache=True,
        )
        assert cache_file.read_bytes() == before

        with pytest.raises(JudgeError, match="cache miss"):
            judge(**_judge_kwargs(prompts, ruleset, tmp_path, sample_index=7, client=None))  # type: ignore[arg-type]

    def test_llm_judge_variance_is_measured(
        self, prompts: JudgePrompts, ruleset: RuleSet, tmp_path: Path
    ) -> None:
        """Five scripted samples (sample_index enters the derivation) produce
        measurable variance — the metric is COMPUTED, not asserted low."""
        runs = [
            judge(**_judge_kwargs(prompts, ruleset, tmp_path, sample_index=index))  # type: ignore[arg-type]
            for index in range(5)
        ]
        verdict_sets = [list(run.violations) for run in runs]
        score = determinism_score(verdict_sets)
        assert 0.0 < score <= 1.0
        keys = {tuple(sorted(v.constraint_id for v in s)) for s in verdict_sets}
        assert len(keys) >= 2  # the scripted judge genuinely varies by sample


class TestEvaluationLock:
    def test_lock_refuses_the_evaluation_run(self, mini_log: OCELLog, tmp_path: Path) -> None:
        split = tmp_path / "split"
        split.mkdir()
        (split / "manifest.json").write_text(
            json.dumps({"run_id": EVALUATION_BASE_RUN_ID}), encoding="utf-8"
        )
        with pytest.raises(EvaluationLockError, match="catalogue-v1"):
            judge_all(split, mini_log, cache_dir=tmp_path / "cache")

    def test_dev_run_passes_and_override_exists(self, mini_log: OCELLog, tmp_path: Path) -> None:
        split = tmp_path / "split"
        split.mkdir()
        (split / "manifest.json").write_text(
            json.dumps({"run_id": "e1f8535c07681d95"}), encoding="utf-8"
        )
        # The mini log's sessions have no span files, so restrict to the ocel
        # condition; scripted provider; one model, one sample.
        runs = judge_all(
            split,
            mini_log,
            models=("claude-haiku-4-5",),
            samples=1,
            conditions=("ocel",),
            cache_dir=tmp_path / "cache",
        )
        assert runs
        assert all(run.condition == "ocel" for run in runs)
        assert {run.session_id for run in runs} == {"SES-A", "SES-B"}

        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "manifest.json").write_text(
            json.dumps({"run_id": EVALUATION_BASE_RUN_ID}), encoding="utf-8"
        )
        override = judge_all(
            locked,
            mini_log,
            models=("claude-haiku-4-5",),
            samples=1,
            conditions=("ocel",),
            cache_dir=tmp_path / "cache2",
            allow_evaluation=True,
        )
        assert override


class TestExcerpt:
    def test_foreign_object_stub_keeps_v2_visible(self, mini_log: OCELLog) -> None:
        """An object referenced by this session but owned by another carries
        "foreign": true — the pre-registered input rule for cross-session
        faults. In the mini log, APP-A's events reference nothing foreign,
        but the shared agents appear in both sessions' excerpts."""
        excerpt_a = json.loads(session_excerpt(mini_log, "SES-A"))
        object_ids = {entry["id"] for entry in excerpt_a["objects"]}
        assert "AGT-INTAKE" in object_ids
        agent = next(e for e in excerpt_a["objects"] if e["id"] == "AGT-INTAKE")
        assert agent.get("foreign") is True  # also acts in SES-B

        decision = next(e for e in excerpt_a["objects"] if e["id"] == "DEC-A")
        assert "foreign" not in decision  # owned exclusively by SES-A

    def test_cross_session_approves_edge_is_in_the_excerpt(self, mini_log: OCELLog) -> None:
        """A V2-shaped log (session A's approval repointed to session B's
        decision): the foreign decision appears as a stub and the approves
        relation is visible in session A's excerpt — V2 judge-visibility by
        construction."""
        import dataclasses

        from auditors_trace.model.log import OCELRelation
        from auditors_trace.model.ocel_schema import Qualifier

        events = []
        for event in mini_log.events:
            if event.event_id == "EV-A-08":
                relations = tuple(
                    OCELRelation(object_id="DEC-B", qualifier=r.qualifier)
                    if r.qualifier is Qualifier.APPROVES
                    else r
                    for r in event.relations
                )
                event = dataclasses.replace(event, relations=relations)
            events.append(event)
        repointed = OCELLog(events=tuple(events), objects=mini_log.objects, o2o=mini_log.o2o)

        excerpt_a = json.loads(session_excerpt(repointed, "SES-A"))
        foreign_decision = next(entry for entry in excerpt_a["objects"] if entry["id"] == "DEC-B")
        assert foreign_decision["foreign"] is True
        approval = next(e for e in excerpt_a["events"] if e["id"] == "EV-A-08")
        assert {"object_id": "DEC-B", "qualifier": "approves"} in approval["relations"]

    def test_excerpt_is_deterministic(self, mini_log: OCELLog) -> None:
        assert session_excerpt(mini_log, "SES-B") == session_excerpt(mini_log, "SES-B")

    def test_unknown_session_raises(self, mini_log: OCELLog) -> None:
        with pytest.raises(JudgeError, match="SES-X"):
            session_excerpt(mini_log, "SES-X")


class TestPrompts:
    def test_rules_block_is_generated_from_the_ruleset(
        self, prompts: JudgePrompts, ruleset: RuleSet
    ) -> None:
        """Drift guard: the rendered system text embeds every rule's id,
        description, and formal statement from rules/rules.yaml verbatim."""
        system_text, _ = render_prompt(
            prompts, ruleset, condition="ocel", session_id="S", payload="P"
        )
        for rule in ruleset.rules:
            assert rule.constraint_id in system_text
            assert rule.description.strip() in system_text
            assert rule.formal.strip() in system_text
        assert render_rules_block(ruleset) in system_text

    def test_prompt_hash_covers_the_frozen_content(
        self, prompts: JudgePrompts, ruleset: RuleSet
    ) -> None:
        edited = prompts.model_copy(
            update={"system_template": prompts.system_template + "\nExtra rule."}
        )
        assert prompt_sha256(edited, ruleset) != prompt_sha256(prompts, ruleset)
        key_a = cache_key("m", 0, "ocel", "h" * 64, prompt_sha256(prompts, ruleset))
        key_b = cache_key("m", 0, "ocel", "h" * 64, prompt_sha256(edited, ruleset))
        assert key_a != key_b

    def test_loader_rejects_placeholder_and_wrong_placeholders(self, tmp_path: Path) -> None:
        bad = tmp_path / "prompts.yaml"
        bad.write_text(
            'schema_version: 1\nprompt_version: "TODO"\n'
            'system_template: "x {rules_block} {output_schema}"\n'
            'user_template_spans: "{session_id} {payload}"\n'
            'user_template_ocel: "{session_id} {payload}"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="placeholder"):
            load_judge_prompts(bad)

        wrong = tmp_path / "prompts2.yaml"
        wrong.write_text(
            'schema_version: 1\nprompt_version: "v1"\n'
            'system_template: "x {rules_block}"\n'  # missing {output_schema}
            'user_template_spans: "{session_id} {payload}"\n'
            'user_template_ocel: "{session_id} {payload}"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="placeholders"):
            load_judge_prompts(wrong)


class TestAggregators:
    def _run(
        self,
        sample_index: int,
        violations: tuple[Violation, ...],
        schema_failure: bool = False,
    ) -> JudgeRun:
        return JudgeRun(
            model_id="claude-haiku-4-5",
            sample_index=sample_index,
            condition="ocel",
            session_id="SESS-0001",
            violations=violations,
            schema_failure=schema_failure,
            cache_key="0" * 64,
            from_cache=True,
        )

    def test_modal_verdict_is_whole_set_agreement(self) -> None:
        agree = (Violation(constraint_id="T1.synchronised_approval", ocel_event_ids=("E1",)),)
        other = (Violation(constraint_id="T4.reason_code_presence", ocel_event_ids=("E2",)),)
        runs = [self._run(0, agree), self._run(1, agree), self._run(2, other)]
        assert modal_verdict(runs) == agree

    def test_schema_failures_form_their_own_modal_class(self) -> None:
        """Three failures beat two agreeing verdicts: the modal outcome is
        'the judge failed', reported as an empty verdict — never as if the
        failing runs had agreed with the empty set."""
        agree = (Violation(constraint_id="T1.synchronised_approval", ocel_event_ids=("E1",)),)
        runs = [
            self._run(0, agree),
            self._run(1, agree),
            self._run(2, (), schema_failure=True),
            self._run(3, (), schema_failure=True),
            self._run(4, (), schema_failure=True),
        ]
        assert modal_verdict(runs) == ()

    def test_majority_vote_is_per_item(self) -> None:
        shared = Violation(constraint_id="T1.synchronised_approval", ocel_event_ids=("E1",))
        rare = Violation(constraint_id="T4.reason_code_presence", ocel_event_ids=("E2",))
        runs = [
            self._run(0, (shared, rare)),
            self._run(1, (shared,)),
            self._run(2, (shared,)),
            self._run(3, ()),
            self._run(4, ()),
        ]
        assert majority_vote(runs, k=3) == (shared,)
        assert majority_vote(runs, k=1) == (shared, rare)

    def test_majority_vote_groups_synonyms_when_matching_given(self) -> None:
        matching = load_judge_matching(
            MATCHING,
            required_ids={
                "T1.synchronised_approval",
                "T2.mandatory_data_coverage",
                "T3.delegation_integrity",
                "T4.reason_code_presence",
                "T5.prohibited_attribute_access",
                "STD.policy_version_current",
            },
        )
        canonical = Violation(constraint_id="T1.synchronised_approval", ocel_event_ids=("E1",))
        synonym = Violation(constraint_id="Missing Human Approval", ocel_event_ids=("E1",))
        runs = [
            self._run(0, (canonical,)),
            self._run(1, (synonym,)),
            self._run(2, (canonical,)),
        ]
        assert len(majority_vote(runs, k=3, matching=matching)) == 1
        assert len(majority_vote(runs, k=3)) == 0  # without the map, labels differ


def test_judge_run_carries_no_volatile_fields() -> None:
    """Cost, latency, usage, and access dates live ONLY in cache records."""
    from dataclasses import fields

    assert {f.name for f in fields(JudgeRun)} == {
        "model_id",
        "sample_index",
        "condition",
        "session_id",
        "violations",
        "schema_failure",
        "cache_key",
        "from_cache",
    }
