"""Phase 3: the ingest CLI and the end-to-end scenario->OCEL pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditors_trace.ingest.__main__ import main

SPANS = Path(__file__).resolve().parent.parent / "golden" / "spans"
GRANT = SPANS / "credit_grant_approval_seed42.jsonl"
DENY = SPANS / "credit_deny_approval_refer_seed2.jsonl"
MESSY = SPANS / "messy_vendor_variants.jsonl"


class TestCliExitCodes:
    def test_no_arguments_is_usage_error(self) -> None:
        assert main([]) == 2

    def test_missing_manifest_is_input_unavailable(self, tmp_path: Path) -> None:
        assert main(["run", "--manifest", str(tmp_path / "ghost.json")]) == 3

    def test_messy_file_is_contract_violation(self, tmp_path: Path) -> None:
        assert (
            main(
                [
                    "run",
                    "--spans",
                    str(MESSY),
                    "--out",
                    str(tmp_path / "out.jsonocel"),
                    "--coverage",
                    str(tmp_path / "coverage.json"),
                ]
            )
            == 4
        )

    def test_duplicate_session_is_integrity_error(self, tmp_path: Path) -> None:
        assert (
            main(
                [
                    "run",
                    "--spans",
                    str(GRANT),
                    str(DENY),
                    "--out",
                    str(tmp_path / "out.jsonocel"),
                    "--coverage",
                    str(tmp_path / "coverage.json"),
                ]
            )
            == 6
        )

    def test_happy_path_writes_all_three_artefacts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "grant.jsonocel"
        coverage = tmp_path / "coverage.json"
        code = main(["run", "--spans", str(GRANT), "--out", str(out), "--coverage", str(coverage)])
        assert code == 0
        assert out.exists()
        assert coverage.exists()
        index = tmp_path / "grant.jsonocel.span_index.json"
        assert index.exists()
        payload = json.loads(coverage.read_text(encoding="utf-8"))
        assert payload["mapped_fraction"] == 1.0
        assert payload["run"] is None  # --spans mode carries no manifest
        assert "mapped 28 events" in capsys.readouterr().out

    def test_xml_serialisation_is_inferred_from_suffix(self, tmp_path: Path) -> None:
        out = tmp_path / "grant.xmlocel"
        code = main(
            [
                "run",
                "--spans",
                str(GRANT),
                "--out",
                str(out),
                "--coverage",
                str(tmp_path / "coverage.json"),
                "--quiet",
            ]
        )
        assert code == 0
        assert out.read_bytes().startswith(b"<?xml")

    def test_bad_out_suffix_is_a_usage_error(self, tmp_path: Path) -> None:
        """A typo'd --out suffix must not masquerade as an OCEL model
        rejection (review finding, 19 Aug 2026)."""
        code = main(
            [
                "run",
                "--spans",
                str(GRANT),
                "--out",
                str(tmp_path / "run.txt"),
                "--coverage",
                str(tmp_path / "c.json"),
                "--quiet",
            ]
        )
        assert code == 2

    def test_integrity_error_with_no_such_in_free_text_stays_exit_6(self, tmp_path: Path) -> None:
        """Exit codes come from exception types, never message sniffing: an
        alias-conflict message embedding the words 'no such' must still be an
        integrity error (review finding, 19 Aug 2026)."""
        rows = [
            json.loads(line)
            for line in (SPANS / "paired_vocabulary_genai.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        for row in rows:
            if "gen_ai.tool.description" in row["attributes"]:
                # Both vocabularies present, disagreeing — an alias conflict
                # whose free text embeds the sniffable words.
                row["attributes"]["tool.description"] = "Fails if no such applicant exists."
        bad = tmp_path / "poisoned.jsonl"
        bad.write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8",
            newline="\n",
        )
        code = main(
            [
                "run",
                "--spans",
                str(bad),
                "--out",
                str(tmp_path / "out.jsonocel"),
                "--coverage",
                str(tmp_path / "c.json"),
                "--quiet",
            ]
        )
        assert code == 6

    def test_written_ocel_reads_back(self, tmp_path: Path) -> None:
        from auditors_trace.model.io import read_ocel

        out = tmp_path / "grant.jsonocel"
        main(
            [
                "run",
                "--spans",
                str(GRANT),
                "--out",
                str(out),
                "--coverage",
                str(tmp_path / "c.json"),
                "--quiet",
            ]
        )
        log = read_ocel(out, "json")
        assert len(log.events) == 28
        assert len(log.objects) == 25


class TestEndToEnd:
    def test_fleet_to_ocel_and_vocabulary_invariance(self, tmp_path: Path) -> None:
        """The strongest both-vocabularies claim: a fleet run with and without
        the derived gen_ai.* attributes maps to the same OCEL log hash."""
        pytest.importorskip("opentelemetry")
        from auditors_trace.model.io import read_ocel
        from auditors_trace.model.log import log_hash
        from auditors_trace.scenario.agents import run_fleet

        seed_data = Path(__file__).resolve().parent.parent / "fixtures"
        sample = seed_data / "german_credit_sample.data"

        hashes: list[str] = []
        for label, genai in (("with", True), ("without", False)):
            span_dir = tmp_path / f"spans_{label}"
            run_fleet(
                2,
                42,
                span_dir,
                provider="scripted",
                genai_semconv=genai,
                seed_data=sample,
                quiet=True,
            )
            out = tmp_path / f"run_{label}.jsonocel"
            coverage = tmp_path / f"coverage_{label}.json"
            code = main(
                [
                    "run",
                    "--manifest",
                    str(span_dir / "manifest.json"),
                    "--out",
                    str(out),
                    "--coverage",
                    str(coverage),
                    "--quiet",
                ]
            )
            assert code == 0
            payload = json.loads(coverage.read_text(encoding="utf-8"))
            assert payload["mapped_fraction"] > 0.9
            assert payload["run"]["genai_semconv"] is genai
            log = read_ocel(out, "json")
            assert len(log.events) == 2 * 28
            hashes.append(log_hash(log))

        assert hashes[0] == hashes[1]
