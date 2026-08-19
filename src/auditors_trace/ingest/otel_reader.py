"""Read OTLP / OpenInference span dumps off disk into a span tree.

Phase 3 deliverable. The reader is deliberately strict: an at-span/1 span
file has exactly one shape (``scenario/telemetry.py`` writes it, schema
version 1), and anything else — a foreign format, a truncated span, an
unknown envelope key, a hash mismatch against the run manifest — raises
:class:`IngestError`. Audit evidence is never coerced, repaired, or
silently dropped.

Attribute dictionaries are carried verbatim (``Mapping[str, object]``):
``model/span_contract.py`` is the single coercion and validation authority
for ``at.*`` content, and ``ingest/attribute_map.py`` for the standard
vocabularies. This module never interprets attribute values.

Tree order note: children are sorted by ``(start_time_unix_nano, span_id)``
per BUILD-PLAN, and that order is used only for enrichment walks. OCEL
event order comes exclusively from ``at.event.seq`` (the simulated clock's
dense per-session sequence) — never from wall-clock span times.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from auditors_trace.model.span_contract import SPAN_CONTRACT_VERSION

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class IngestError(ValueError):
    """Span-file, manifest, or tree-level input is unusable. Raised, never repaired."""


#: The exact top-level keys of a schema-version-1 span line, as written by
#: ``scenario/telemetry.span_to_dict`` plus the exporter's ``creation_index``.
_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "trace_id",
        "span_id",
        "parent_span_id",
        "trace_flags",
        "trace_state",
        "name",
        "kind",
        "start_time_unix_nano",
        "end_time_unix_nano",
        "status",
        "attributes",
        "events",
        "links",
        "resource",
        "scope",
        "dropped",
        "creation_index",
    }
)

_SPAN_FILE_SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class Span:
    """A single OTel span, envelope typed, attributes verbatim."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    status_code: str
    attributes: Mapping[str, object]
    scope_name: str
    creation_index: int
    source: str


@dataclass(frozen=True, slots=True)
class SpanTree:
    """A trace: one root span and its descendants, parent links resolved."""

    trace_id: str
    root: Span
    spans: tuple[Span, ...]
    children: Mapping[str, tuple[Span, ...]]


@dataclass(frozen=True, slots=True)
class ManifestView:
    """The run manifest as the reader sees it.

    Deliberately not ``scenario/telemetry.RunManifest``: the ingest side is
    core-only (invariant I2) and must not import the scenario extra.
    """

    schema_version: int
    contract: str
    run_id: str
    scenario_name: str
    scenario_version: str
    seed: int
    session_count: int
    catalogue_sha256: str
    seed_data_sha256: str
    model_id: str
    provider: str
    genai_semconv: bool
    files: tuple[tuple[str, str], ...]


def _parse_row(row: object, source: str) -> Span:
    if not isinstance(row, dict):
        raise IngestError(f"{source}: span line is not a JSON object")
    keys = set(row)
    unknown = keys - _ENVELOPE_KEYS
    if unknown:
        raise IngestError(
            f"{source}: unknown envelope key(s) {sorted(unknown)}; this is not an "
            "at-span/1 schema-version-1 span file"
        )
    missing = _ENVELOPE_KEYS - keys
    if missing:
        raise IngestError(f"{source}: missing envelope key(s) {sorted(missing)}")
    if row["schema_version"] != _SPAN_FILE_SCHEMA_VERSION:
        raise IngestError(
            f"{source}: schema_version {row['schema_version']!r} is not {_SPAN_FILE_SCHEMA_VERSION}"
        )
    dropped = row["dropped"]
    if not isinstance(dropped, dict) or any(dropped.get(k) != 0 for k in dropped):
        raise IngestError(
            f"{source}: non-zero dropped counters {dropped!r}; a truncated span "
            "cannot serve as audit evidence"
        )
    if row["events"]:
        raise IngestError(
            f"{source}: span carries span-events; at-span/1 evidence has none and "
            "dropping them silently is not an option"
        )
    if row["links"]:
        raise IngestError(f"{source}: span carries links; at-span/1 evidence has none")
    attributes = row["attributes"]
    if not isinstance(attributes, dict):
        raise IngestError(f"{source}: attributes is not an object")
    status = row["status"]
    scope = row["scope"]
    if not isinstance(status, dict) or not isinstance(scope, dict):
        raise IngestError(f"{source}: status/scope is not an object")
    parent = row["parent_span_id"]
    return Span(
        trace_id=str(row["trace_id"]),
        span_id=str(row["span_id"]),
        parent_span_id=None if parent is None else str(parent),
        name=str(row["name"]),
        kind=str(row["kind"]),
        start_time_unix_nano=int(row["start_time_unix_nano"]),
        end_time_unix_nano=int(row["end_time_unix_nano"]),
        status_code=str(status.get("code", "")),
        attributes=MappingProxyType(dict(attributes)),
        scope_name=str(scope.get("name", "")),
        creation_index=int(row["creation_index"]),
        source=source,
    )


def read_spans(path: Path) -> tuple[Span, ...]:
    """Read one JSONL span dump, sorted by ``(trace_id, creation_index)``."""
    if not path.exists():
        raise IngestError(f"no such span file: {path}")
    spans: list[Span] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        source = f"{path.name}:{lineno}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestError(f"{source}: not valid JSON: {exc}") from exc
        spans.append(_parse_row(row, source))

    seen_ids: set[str] = set()
    seen_index: set[tuple[str, int]] = set()
    for span in spans:
        if span.span_id in seen_ids:
            raise IngestError(f"{span.source}: duplicate span_id {span.span_id}")
        seen_ids.add(span.span_id)
        index_key = (span.trace_id, span.creation_index)
        if index_key in seen_index:
            raise IngestError(
                f"{span.source}: duplicate creation_index {span.creation_index} "
                f"in trace {span.trace_id}"
            )
        seen_index.add(index_key)
    return tuple(sorted(spans, key=lambda s: (s.trace_id, s.creation_index)))


def read_manifest(path: Path) -> ManifestView:
    """Read and validate a run manifest."""
    if not path.exists():
        raise IngestError(f"no such manifest: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"{path.name}: not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise IngestError(f"{path.name}: manifest is not a JSON object")
    try:
        view = ManifestView(
            schema_version=int(doc["schema_version"]),
            contract=str(doc["contract"]),
            run_id=str(doc["run_id"]),
            scenario_name=str(doc["scenario_name"]),
            scenario_version=str(doc["scenario_version"]),
            seed=int(doc["seed"]),
            session_count=int(doc["session_count"]),
            catalogue_sha256=str(doc["catalogue_sha256"]),
            seed_data_sha256=str(doc["seed_data_sha256"]),
            model_id=str(doc["model_id"]),
            provider=str(doc["provider"]),
            genai_semconv=bool(doc["genai_semconv"]),
            files=tuple((str(name), str(digest)) for name, digest in doc["files"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestError(f"{path.name}: malformed manifest: {exc!r}") from exc
    if view.contract != SPAN_CONTRACT_VERSION:
        raise IngestError(
            f"{path.name}: contract {view.contract!r} is not {SPAN_CONTRACT_VERSION!r}; "
            "refusing to map spans written under a different contract"
        )
    return view


def read_run(manifest_path: Path) -> tuple[Span, ...]:
    """Read every span file the manifest lists, hash-verified. Never listdir."""
    manifest = read_manifest(manifest_path)
    base = manifest_path.parent
    all_spans: list[Span] = []
    for filename, expected_digest in manifest.files:
        file_path = base / filename
        if not file_path.exists():
            raise IngestError(f"manifest lists {filename} but it does not exist in {base}")
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if digest != expected_digest:
            raise IngestError(
                f"{filename}: sha256 mismatch against the manifest (file {digest}, "
                f"manifest {expected_digest}); the run is not intact"
            )
        all_spans.extend(read_spans(file_path))
    return tuple(sorted(all_spans, key=lambda s: (s.trace_id, s.creation_index)))


def build_span_trees(spans: tuple[Span, ...]) -> tuple[SpanTree, ...]:
    """Group spans into per-trace trees.

    Every non-root span must have a resolvable parent; an unresolvable parent
    is an error, never a silent drop.
    """
    by_trace: dict[str, list[Span]] = {}
    for span in spans:
        by_trace.setdefault(span.trace_id, []).append(span)

    trees: list[SpanTree] = []
    for trace_id in sorted(by_trace):
        members = by_trace[trace_id]
        ids = {span.span_id for span in members}
        roots = [span for span in members if span.parent_span_id is None]
        if len(roots) != 1:
            raise IngestError(f"trace {trace_id} has {len(roots)} root spans; exactly one expected")
        children: dict[str, list[Span]] = {}
        for span in members:
            if span.parent_span_id is None:
                continue
            if span.parent_span_id not in ids:
                raise IngestError(
                    f"{span.source}: parent span {span.parent_span_id} is not in "
                    f"trace {trace_id}; an orphan span cannot be evidence"
                )
            children.setdefault(span.parent_span_id, []).append(span)
        sorted_children = {
            parent: tuple(sorted(kids, key=lambda s: (s.start_time_unix_nano, s.span_id)))
            for parent, kids in children.items()
        }
        reachable: set[str] = {roots[0].span_id}
        frontier = [roots[0].span_id]
        while frontier:
            parent_id = frontier.pop()
            for child in sorted_children.get(parent_id, ()):
                reachable.add(child.span_id)
                frontier.append(child.span_id)
        if reachable != ids:
            unreachable = sorted(ids - reachable)
            raise IngestError(
                f"trace {trace_id}: span(s) {unreachable} are unreachable from the "
                "root (parent cycle)"
            )
        trees.append(
            SpanTree(
                trace_id=trace_id,
                root=roots[0],
                spans=tuple(members),
                children=MappingProxyType(sorted_children),
            )
        )
    return tuple(trees)
