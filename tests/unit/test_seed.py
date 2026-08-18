"""Seed loading is reproducible and the selection is a prefix-stable draw.

The committed fixture is a 20-row sample in german.data format (Statlog
German Credit, UCI 144, CC BY 4.0 — Hofmann 1994). The full-file digest test
is network-gated; the hermetic suite runs entirely off the fixture.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from auditors_trace.scenario.seed import (
    GERMAN_CREDIT_DATA_SHA256,
    application_id,
    decode_tables,
    demographic_projection,
    download_seed,
    load_applicants,
    load_rows,
    seed_digest,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "german_credit_sample.data"


class TestLoadRows:
    def test_parses_all_rows(self) -> None:
        rows = load_rows(FIXTURE)
        assert len(rows) == 20
        assert all(r.credit_amount_dm > 0 for r in rows)
        assert all(r.ground_truth_risk in ("good", "bad") for r in rows)

    def test_decodes_personal_status(self) -> None:
        rows = load_rows(FIXTURE)
        assert {r.sex for r in rows} == {"male", "female"}
        # A95 (female:single) has zero rows in the real dataset; the decode
        # table still maps it, but nothing may assume it appears.
        assert "A95" in decode_tables()["sex"]

    def test_rejects_malformed_row(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.data"
        bad.write_text("A11 6 A34\n", encoding="utf-8")
        with pytest.raises(ValueError, match="expected 21 fields"):
            load_rows(bad)


class TestSameSeedSameSequence:
    def test_same_seed_produces_same_applicant_sequence(self) -> None:
        first = load_applicants(FIXTURE, 15, 42)
        second = load_applicants(FIXTURE, 15, 42)
        assert first == second

    def test_different_seed_produces_different_order(self) -> None:
        assert [a.row_index for a in load_applicants(FIXTURE, 15, 42)] != [
            a.row_index for a in load_applicants(FIXTURE, 15, 43)
        ]

    def test_short_draw_is_prefix_of_long_draw(self) -> None:
        # Phase 8 needs this: the 50-session selection is a strict prefix of
        # the 5000-session selection.
        short = load_applicants(FIXTURE, 5, 42)
        long = load_applicants(FIXTURE, 20, 42)
        assert long[:5] == short

    def test_pseudonyms_are_seed_derived_and_unique(self) -> None:
        apps = load_applicants(FIXTURE, 20, 42)
        ids = [a.applicant_id for a in apps]
        assert len(set(ids)) == 20
        assert all(i.startswith("APN-") for i in ids)
        assert application_id(apps[0], 42) != application_id(apps[0], 43)

    def test_oversampling_wraps_with_distinct_pseudonyms(self) -> None:
        apps = load_applicants(FIXTURE, 25, 42)
        assert len(apps) == 25
        assert len({a.applicant_id for a in apps}) == 25


class TestDemographicProjection:
    def test_projection_carries_only_the_proxy_block(self) -> None:
        applicant = load_applicants(FIXTURE, 1, 42)[0]
        projection = demographic_projection(applicant)
        assert set(projection) == {
            "applicant_id",
            "sex",
            "marital_status",
            "age_years",
            "foreign_worker",
        }
        assert "ground_truth_risk" not in projection


def _fixture_digest_is_not_the_real_file() -> bool:
    return seed_digest(FIXTURE) != GERMAN_CREDIT_DATA_SHA256


class TestDownload:
    def test_corrupt_cache_is_rejected_never_used(self, tmp_path: Path) -> None:
        (tmp_path / "german.data").write_text("tampered\n", encoding="utf-8")
        with pytest.raises(ValueError, match="digest mismatch"):
            download_seed(tmp_path)

    @pytest.mark.network
    @pytest.mark.skipif(
        os.environ.get("AUDITORS_TRACE_NETWORK_TESTS") != "1",
        reason="set AUDITORS_TRACE_NETWORK_TESTS=1 to run the real download",
    )
    def test_seed_download_and_digest(self, tmp_path: Path) -> None:
        path = download_seed(tmp_path)
        assert seed_digest(path) == GERMAN_CREDIT_DATA_SHA256
        rows = load_rows(path)
        assert len(rows) == 1000
        assert sum(1 for r in rows if r.ground_truth_risk == "good") == 700
        assert (tmp_path / "CITATION.txt").exists()


# The dataset label's containment is mutation-tested in test_policy.py
# (assess, and every scripted_message role) — not asserted vacuously here.
