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


class InputUnavailableError(IngestError):
    """The caller-named input does not exist at all.

    A distinct type so the CLI can report "input unavailable" (exit 3)
    without sniffing message text; everything else stays an integrity error
    (exit 6). A file the MANIFEST names but that is missing is deliberately
    an :class:`IngestError` — the run is present but not intact.
    """


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


#: Exact key sets the writer emits inside the nested envelope objects.
_DROPPED_KEYS: Final[frozenset[str]] = frozenset({"attributes", "events", "links"})
_STATUS_KEYS: Final[frozenset[str]] = frozenset({"code", "description"})
_SCOPE_KEYS: Final[frozenset[str]] = frozenset({"name", "version", "schema_url"})


def _require_str(source: str, key: str, value: object, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise IngestError(
            f"{source}: {key} must be a string, got {type(value).__name__}; "
            "audit evidence is never coerced"
        )
    if not allow_empty and not value:
        raise IngestError(f"{source}: {key} is empty")
    return value


def _require_int(source: str, key: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError(
            f"{source}: {key} must be an integer, got {type(value).__name__}; "
            "silent numeric coercion would alter evidence"
        )
    if value < minimum:
        raise IngestError(f"{source}: {key} is {value}, below the minimum {minimum}")
    return value


def _require_exact_object(
    source: str, key: str, value: object, expected_keys: frozenset[str]
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise IngestError(f"{source}: {key} is not an object")
    if set(value) != expected_keys:
        raise IngestError(
            f"{source}: {key} keys {sorted(value)} are not exactly {sorted(expected_keys)}; "
            "this is not an at-span/1 schema-version-1 span file"
        )
    return value


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
    dropped = _require_exact_object(source, "dropped", row["dropped"], _DROPPED_KEYS)
    for counter, raw in dropped.items():
        if isinstance(raw, bool) or not isinstance(raw, int) or raw != 0:
            raise IngestError(
                f"{source}: dropped.{counter} is {raw!r}, not the integer 0; a "
                "truncated span cannot serve as audit evidence"
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
    status = _require_exact_object(source, "status", row["status"], _STATUS_KEYS)
    scope = _require_exact_object(source, "scope", row["scope"], _SCOPE_KEYS)
    parent = row["parent_span_id"]
    if parent is not None and not isinstance(parent, str):
        raise IngestError(
            f"{source}: parent_span_id must be a string or null, got {type(parent).__name__}"
        )
    return Span(
        trace_id=_require_str(source, "trace_id", row["trace_id"], allow_empty=False),
        span_id=_require_str(source, "span_id", row["span_id"], allow_empty=False),
        parent_span_id=parent,
        name=_require_str(source, "name", row["name"]),
        kind=_require_str(source, "kind", row["kind"]),
        start_time_unix_nano=_require_int(
            source, "start_time_unix_nano", row["start_time_unix_nano"], minimum=0
        ),
        end_time_unix_nano=_require_int(
            source, "end_time_unix_nano", row["end_time_unix_nano"], minimum=0
        ),
        status_code=_require_str(source, "status.code", status["code"]),
        attributes=MappingProxyType(dict(attributes)),
        scope_name=_require_str(source, "scope.name", scope["name"]),
        creation_index=_require_int(source, "creation_index", row["creation_index"], minimum=1),
        source=source,
    )


def ensure_unique_spans(spans: tuple[Span, ...]) -> tuple[Span, ...]:
    """Reject duplicate span ids or (trace, creation_index) pairs, run-wide.

    Applied per file by :func:`read_spans` AND across the concatenation of
    files by :func:`read_run` and the CLI's ``--spans`` path — a span that
    appears twice via two hash-valid files would otherwise silently corrupt
    the span-index evidence sidecar.
    """
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
    return spans


def read_spans(path: Path) -> tuple[Span, ...]:
    """Read one JSONL span dump, sorted by ``(trace_id, creation_index)``.

    Lines are split on ``\\n`` only — the writer's sole terminator
    (``newline="\\n"``). ``str.splitlines`` would also split on U+2028/
    U+2029/U+0085, which legally occur raw inside JSON string values under
    the house canonical JSON (``ensure_ascii=False``).
    """
    if not path.exists():
        raise InputUnavailableError(f"no such span file: {path}")
    spans: list[Span] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        source = f"{path.name}:{lineno}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestError(f"{source}: not valid JSON: {exc}") from exc
        spans.append(_parse_row(row, source))
    ensure_unique_spans(tuple(spans))
    return tuple(sorted(spans, key=lambda s: (s.trace_id, s.creation_index)))


_MANIFEST_SCHEMA_VERSION: Final[int] = 1


def _manifest_str(name: str, doc: dict[str, object], key: str) -> str:
    value = doc[key]
    if not isinstance(value, str):
        raise IngestError(f"{name}: manifest field {key} must be a string, got {value!r}")
    return value


def _manifest_int(name: str, doc: dict[str, object], key: str) -> int:
    value = doc[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError(f"{name}: manifest field {key} must be an integer, got {value!r}")
    return value


def read_manifest(path: Path) -> ManifestView:
    """Read and validate a run manifest. Strictly typed — never coerced."""
    if not path.exists():
        raise InputUnavailableError(f"no such manifest: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"{path.name}: not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise IngestError(f"{path.name}: manifest is not a JSON object")
    name = path.name
    try:
        genai_semconv = doc["genai_semconv"]
        if not isinstance(genai_semconv, bool):
            raise IngestError(
                f"{name}: manifest field genai_semconv must be a boolean, got "
                f"{genai_semconv!r} (bool('false') is True — coercion would "
                "mis-record provenance)"
            )
        view = ManifestView(
            schema_version=_manifest_int(name, doc, "schema_version"),
            contract=_manifest_str(name, doc, "contract"),
            run_id=_manifest_str(name, doc, "run_id"),
            scenario_name=_manifest_str(name, doc, "scenario_name"),
            scenario_version=_manifest_str(name, doc, "scenario_version"),
            seed=_manifest_int(name, doc, "seed"),
            session_count=_manifest_int(name, doc, "session_count"),
            catalogue_sha256=_manifest_str(name, doc, "catalogue_sha256"),
            seed_data_sha256=_manifest_str(name, doc, "seed_data_sha256"),
            model_id=_manifest_str(name, doc, "model_id"),
            provider=_manifest_str(name, doc, "provider"),
            genai_semconv=genai_semconv,
            files=tuple((str(entry[0]), str(entry[1])) for entry in doc["files"]),
        )
    except (KeyError, TypeError, IndexError) as exc:
        raise IngestError(f"{path.name}: malformed manifest: {exc!r}") from exc
    if view.schema_version != _MANIFEST_SCHEMA_VERSION:
        raise IngestError(
            f"{path.name}: manifest schema_version {view.schema_version} is not "
            f"{_MANIFEST_SCHEMA_VERSION}; a future format must not be silently "
            "interpreted under v1 semantics"
        )
    if view.contract != SPAN_CONTRACT_VERSION:
        raise IngestError(
            f"{path.name}: contract {view.contract!r} is not {SPAN_CONTRACT_VERSION!r}; "
            "refusing to map spans written under a different contract"
        )
    return view


def read_run(manifest_path: Path) -> tuple[Span, ...]:
    """Read every span file the manifest lists, hash-verified. Never listdir.

    Filenames differing only by case are rejected on every platform: a
    case-insensitive filesystem (Windows) would read one file twice while a
    case-sensitive one errors — the same bytes must behave identically
    everywhere. The distinct-session count is cross-checked against the
    manifest's ``session_count``, which the coverage report echoes as
    provenance.
    """
    manifest = read_manifest(manifest_path)
    base = manifest_path.parent
    folded: dict[str, str] = {}
    for filename, _ in manifest.files:
        key = filename.casefold()
        if key in folded:
            raise IngestError(
                f"manifest lists {folded[key]!r} and {filename!r}, which differ only "
                "by case; case-insensitive filesystems would read one file twice"
            )
        folded[key] = filename
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
    spans = tuple(sorted(all_spans, key=lambda s: (s.trace_id, s.creation_index)))
    ensure_unique_spans(spans)
    traces = {span.trace_id for span in spans}
    if len(traces) != manifest.session_count:
        raise IngestError(
            f"manifest declares session_count={manifest.session_count} but the span "
            f"files contain {len(traces)} trace(s); the run is not intact"
        )
    return spans


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
