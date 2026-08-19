"""Phase 3: the span-file reader and tree builder.

The reader is strict by design: at-span/1 evidence files have exactly one
shape, and anything else raises ``IngestError`` — a foreign or corrupted
span dump must never be silently coerced into audit evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from auditors_trace.ingest.otel_reader import (
    IngestError,
    Span,
    build_span_trees,
    read_manifest,
    read_run,
    read_spans,
)

SPANS = Path(__file__).resolve().parent.parent / "golden" / "spans"
GRANT = SPANS / "credit_grant_approval_seed42.jsonl"
DENY = SPANS / "credit_deny_approval_refer_seed2.jsonl"
MESSY = SPANS / "messy_vendor_variants.jsonl"
GRANT_MANIFEST = SPANS / "credit_grant_approval_seed42.manifest.json"
DENY_MANIFEST = SPANS / "credit_deny_approval_refer_seed2.manifest.json"


def _rewrite(src: Path, tmp_path: Path, mutate: Any) -> Path:
    """Copy ``src`` line by line through ``mutate(rows) -> rows``."""
    rows = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines()]
    out = tmp_path / src.name
    mutated = mutate(rows)
    out.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in mutated),
        encoding="utf-8",
        newline="\n",
    )
    return out


class TestReadSpans:
    @pytest.mark.parametrize("path", [GRANT, DENY], ids=["grant", "deny"])
    def test_golden_files_read_fully(self, path: Path) -> None:
        spans = read_spans(path)
        assert len(spans) == 42
        assert all(isinstance(s, Span) for s in spans)
        assert [s.creation_index for s in spans] == list(range(1, 43))
        roots = [s for s in spans if s.parent_span_id is None]
        assert len(roots) == 1
        assert roots[0].creation_index == 1

    def test_messy_file_comes_back_in_creation_order(self) -> None:
        # The file is deliberately out of order (root last); the reader sorts.
        spans = read_spans(MESSY)
        assert [s.creation_index for s in spans] == [1, 2, 3, 4, 5, 6]
        assert spans[0].parent_span_id is None

    def test_attributes_are_verbatim_and_immutable(self) -> None:
        spans = read_spans(GRANT)
        root = spans[0]
        assert root.attributes["at.span.role"] == "session_root"
        with pytest.raises(TypeError):
            root.attributes["x"] = 1  # type: ignore[index]

    def test_source_names_file_and_line(self) -> None:
        spans = read_spans(MESSY)
        assert all(s.source.startswith("messy_vendor_variants.jsonl:") for s in spans)

    def test_wrong_schema_version_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[0]["schema_version"] = 2
            return rows

        with pytest.raises(IngestError, match="schema_version"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_missing_envelope_key_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            del rows[3]["creation_index"]
            return rows

        with pytest.raises(IngestError, match="creation_index"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_unknown_envelope_key_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[2]["vendor_extension"] = {"x": 1}
            return rows

        with pytest.raises(IngestError, match="vendor_extension"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_nonzero_dropped_counter_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[5]["dropped"]["attributes"] = 3
            return rows

        with pytest.raises(IngestError, match="dropped"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_nonempty_span_events_raise(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[4]["events"] = [{"name": "exception"}]
            return rows

        with pytest.raises(IngestError, match="events"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_duplicate_span_id_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[7]["span_id"] = rows[6]["span_id"]
            return rows

        with pytest.raises(IngestError, match=r"span_id|span id"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_duplicate_creation_index_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[7]["creation_index"] = rows[6]["creation_index"]
            return rows

        with pytest.raises(IngestError, match="creation_index"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        from auditors_trace.ingest.otel_reader import InputUnavailableError

        with pytest.raises(InputUnavailableError, match="no such"):
            read_spans(tmp_path / "ghost.jsonl")

    # --- review findings, 19 Aug 2026 ---------------------------------

    def test_unicode_line_separator_in_attribute_survives(self, tmp_path: Path) -> None:
        """U+2028 inside a JSON string is legal writer output (canonical JSON
        uses ensure_ascii=False); str.splitlines would split mid-string."""

        poisoned = "credit" + chr(0x2028) + "application"

        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[0]["attributes"]["at.session.workflow"] = poisoned
            return rows

        rows = [json.loads(line) for line in GRANT.read_text(encoding="utf-8").splitlines()]
        mutated = mutate(rows)
        out = tmp_path / "u2028.jsonl"
        out.write_text(
            "".join(
                json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
                for r in mutated
            ),
            encoding="utf-8",
            newline="\n",
        )
        spans = read_spans(out)
        assert len(spans) == 42
        assert spans[0].attributes["at.session.workflow"] == poisoned

    @pytest.mark.parametrize("bad", [1.9, "7", -3, 0, True])
    def test_non_integer_creation_index_raises(self, bad: Any, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[3]["creation_index"] = bad
            return rows

        with pytest.raises(IngestError, match="creation_index"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_float_timestamp_raises_never_truncates(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[3]["start_time_unix_nano"] = 1787063404059312900.0
            return rows

        with pytest.raises(IngestError, match="start_time_unix_nano"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    @pytest.mark.parametrize(
        "dropped",
        [
            {},
            {"attributes": 0},
            {"attributes": 0, "events": 0, "links": 0, "bogus": 0},
            {"attributes": False, "events": 0.0, "links": 0},
        ],
        ids=["empty", "missing-keys", "extra-key", "type-laundered"],
    )
    def test_degenerate_dropped_shapes_raise(self, dropped: dict[str, Any], tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[5]["dropped"] = dropped
            return rows

        with pytest.raises(IngestError, match="dropped"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_null_trace_id_raises_never_becomes_the_string_none(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for row in rows:
                row["trace_id"] = None
            return rows

        with pytest.raises(IngestError, match="trace_id"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))

    def test_status_without_code_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[2]["status"] = {}
            return rows

        with pytest.raises(IngestError, match="status"):
            read_spans(_rewrite(GRANT, tmp_path, mutate))


class TestBuildSpanTrees:
    @pytest.mark.parametrize("path", [GRANT, DENY], ids=["grant", "deny"])
    def test_one_tree_per_golden_session(self, path: Path) -> None:
        trees = build_span_trees(read_spans(path))
        assert len(trees) == 1
        tree = trees[0]
        assert tree.root.parent_span_id is None
        assert tree.root.attributes.get("at.span.role") == "session_root"
        assert len(tree.spans) == 42
        reachable = {tree.root.span_id}
        frontier = [tree.root.span_id]
        while frontier:
            parent = frontier.pop()
            for child in tree.children.get(parent, ()):
                reachable.add(child.span_id)
                frontier.append(child.span_id)
        assert len(reachable) == 42

    def test_children_sorted_by_start_time_then_span_id(self) -> None:
        tree = build_span_trees(read_spans(GRANT))[0]
        for children in tree.children.values():
            keys = [(c.start_time_unix_nano, c.span_id) for c in children]
            assert keys == sorted(keys)

    def test_unresolvable_parent_raises(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[10]["parent_span_id"] = "feedfacefeedface"
            return rows

        spans = read_spans(_rewrite(GRANT, tmp_path, mutate))
        with pytest.raises(IngestError, match="parent"):
            build_span_trees(spans)

    def test_two_roots_raise(self, tmp_path: Path) -> None:
        def mutate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows[10]["parent_span_id"] = None
            return rows

        spans = read_spans(_rewrite(GRANT, tmp_path, mutate))
        with pytest.raises(IngestError, match="root"):
            build_span_trees(spans)


class TestManifest:
    def test_read_both_committed_manifests(self) -> None:
        for path, seed in ((GRANT_MANIFEST, 42), (DENY_MANIFEST, 2)):
            view = read_manifest(path)
            assert view.contract == "at-span/1"
            assert view.seed == seed
            assert view.session_count == 1
            assert view.files
            assert all(len(f) == 2 for f in view.files)

    def test_unknown_contract_raises(self, tmp_path: Path) -> None:
        doc = json.loads(GRANT_MANIFEST.read_text(encoding="utf-8"))
        doc["contract"] = "at-span/999"
        bad = tmp_path / "manifest.json"
        bad.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        with pytest.raises(IngestError, match="contract"):
            read_manifest(bad)

    def test_read_run_happy_path(self, tmp_path: Path) -> None:
        # Recreate the generator's layout: the manifest names SESS-0000.jsonl.
        shutil.copy(GRANT_MANIFEST, tmp_path / "manifest.json")
        shutil.copy(GRANT, tmp_path / "SESS-0000.jsonl")
        spans = read_run(tmp_path / "manifest.json")
        assert len(spans) == 42

    def test_read_run_detects_a_flipped_byte(self, tmp_path: Path) -> None:
        shutil.copy(GRANT_MANIFEST, tmp_path / "manifest.json")
        payload = GRANT.read_bytes().replace(b"session_root", b"session_r00t", 1)
        (tmp_path / "SESS-0000.jsonl").write_bytes(payload)
        assert hashlib.sha256(payload).hexdigest() != hashlib.sha256(GRANT.read_bytes()).hexdigest()
        with pytest.raises(IngestError, match="sha256"):
            read_run(tmp_path / "manifest.json")

    def test_read_run_missing_file_raises(self, tmp_path: Path) -> None:
        shutil.copy(GRANT_MANIFEST, tmp_path / "manifest.json")
        with pytest.raises(IngestError, match="SESS-0000"):
            read_run(tmp_path / "manifest.json")

    # --- review findings, 19 Aug 2026 ---------------------------------

    def _edited_manifest(self, tmp_path: Path, **overrides: Any) -> Path:
        doc = json.loads(GRANT_MANIFEST.read_text(encoding="utf-8"))
        doc.update(overrides)
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        return path

    def test_string_genai_semconv_raises_never_coerces(self, tmp_path: Path) -> None:
        # bool("false") is True — coercion would mis-record provenance.
        with pytest.raises(IngestError, match="genai_semconv"):
            read_manifest(self._edited_manifest(tmp_path, genai_semconv="false"))

    def test_float_seed_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="seed"):
            read_manifest(self._edited_manifest(tmp_path, seed=42.9))

    def test_future_manifest_schema_version_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="schema_version"):
            read_manifest(self._edited_manifest(tmp_path, schema_version=999))

    def test_session_count_mismatch_raises(self, tmp_path: Path) -> None:
        # The coverage report echoes session_count as provenance; an edited
        # manifest must not survive to a report asserting 100 sessions.
        shutil.copy(GRANT, tmp_path / "SESS-0000.jsonl")
        self._edited_manifest(tmp_path, session_count=100)
        with pytest.raises(IngestError, match="session_count"):
            read_run(tmp_path / "manifest.json")

    def test_case_colliding_filenames_raise_on_every_platform(self, tmp_path: Path) -> None:
        doc = json.loads(GRANT_MANIFEST.read_text(encoding="utf-8"))
        doc["files"] = [doc["files"][0], ["SESS-0000.JSONL", doc["files"][0][1]]]
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        shutil.copy(GRANT, tmp_path / "SESS-0000.jsonl")
        with pytest.raises(IngestError, match="case"):
            read_run(path)

    def test_cross_file_duplicate_span_raises(self, tmp_path: Path) -> None:
        """A span duplicated via a second hash-valid file must not silently
        corrupt the span-index sidecar."""
        import hashlib as _hashlib

        rows = [json.loads(line) for line in GRANT.read_text(encoding="utf-8").splitlines()]
        dupe = dict(rows[5])  # a layer-B span
        dupe["creation_index"] = 999
        extra = tmp_path / "extra.jsonl"
        extra.write_text(
            json.dumps(dupe, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        shutil.copy(GRANT, tmp_path / "SESS-0000.jsonl")
        doc = json.loads(GRANT_MANIFEST.read_text(encoding="utf-8"))
        doc["files"] = [
            doc["files"][0],
            ["extra.jsonl", _hashlib.sha256(extra.read_bytes()).hexdigest()],
        ]
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        with pytest.raises(IngestError, match="duplicate span_id"):
            read_run(manifest)
