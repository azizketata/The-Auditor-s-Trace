"""TracerProvider construction, deterministic ids, and the JSONL span sink.

Everything that makes the span files byte-reproducible and re-readable lives
here. The non-obvious choices are each forced by a verified fact about the
pinned dependencies:

- ``SpanLimits(max_span_attributes=1024)`` — the default is 128 and truncates
  *silently* into a dropped counter; a truncated audit span is unacceptable,
  so any non-zero dropped count fails the run.
- ``SimpleSpanProcessor``, not Batch — Batch's worker timer and bounded queue
  make on-disk order nondeterministic and can drop the tail of a run.
- A hand-rolled ``span_to_dict`` — ``ReadableSpan.to_json()`` truncates
  nanoseconds to microseconds, serialises kind as ``"SpanKind.CLIENT"``,
  ``repr()``s trace state, and drops the instrumentation scope entirely.
- Files opened with ``newline="\\n"`` — Windows CRLF translation would change
  file hashes relative to Linux CI.
- ``service.instance.id`` pinned to the run id — ``Resource.create()`` would
  otherwise inject a fresh uuid4 per process.
- A deterministic ``IdGenerator`` on the provider governs BOTH span layers:
  the OpenInference tracer only substitutes its own generator when the
  configured one is exactly ``RandomIdGenerator``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from openinference.instrumentation import TraceConfig
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.trace import Tracer

from auditors_trace.model.span_contract import (
    OPENINFERENCE_SESSION_ID,
    SESSION_ID,
    SPAN_CONTRACT_VERSION,
)

SPAN_FILE_SCHEMA_VERSION: Final[int] = 1


class SpanTruncationError(RuntimeError):
    """A span silently dropped attributes, events, or links."""


class DeterministicIdGenerator(IdGenerator):
    """Content-addressed trace and span ids.

    ``trace_id = sha256(run_id | session_id)[:16 bytes]`` and
    ``span_id = sha256(run_id | session_id | counter)[:8 bytes]``, with the
    counter reset per session — so ids survive reordering of sessions and the
    span files become genuine golden fixtures, byte-identical across runs.

    Ordering never depends on these ids: ``at.event.seq`` is the ordering key.
    """

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._session_id = ""
        self._counter = 0
        self._creation_order: dict[int, int] = {}

    def begin_session(self, session_id: str) -> None:
        """Reset the counter for a new session's trace."""
        self._session_id = session_id
        self._counter = 0

    def generate_trace_id(self) -> int:
        if not self._session_id:
            raise RuntimeError("begin_session() must be called before span creation")
        digest = hashlib.sha256(f"{self._run_id}|{self._session_id}".encode()).digest()
        trace_id = int.from_bytes(digest[:16], "big")
        return trace_id or 1

    def generate_span_id(self) -> int:
        if not self._session_id:
            raise RuntimeError("begin_session() must be called before span creation")
        self._counter += 1
        digest = hashlib.sha256(
            f"{self._run_id}|{self._session_id}|{self._counter}".encode()
        ).digest()
        span_id = int.from_bytes(digest[:8], "big") or 1
        if span_id in self._creation_order:
            raise RuntimeError(f"span id collision within session {self._session_id}")
        self._creation_order[span_id] = self._counter
        return span_id

    def issued_count(self) -> int:
        """How many span ids this generator has issued over the whole run."""
        return len(self._creation_order)

    def creation_index(self, span_id: int) -> int:
        """The per-session creation counter for a span id.

        This is the deterministic on-disk ordering key: execution order is
        reproducible even though span wall-clock nanoseconds (real telemetry)
        are not. Sorting by wall time would tie constantly at 15.625 ms
        resolution and fall through to a hash-valued span id — a random order.
        """
        try:
            return self._creation_order[span_id]
        except KeyError as exc:
            raise RuntimeError(f"span id {span_id:#x} was not issued by this generator") from exc


def span_to_dict(s: ReadableSpan) -> dict[str, Any]:
    """A lossless, round-trippable encoding of one span."""
    ctx = s.get_span_context()
    if ctx is None:
        raise SpanTruncationError(f"span {s.name!r} has no span context")
    parent = s.parent
    attributes = {
        k: (list(v) if isinstance(v, (tuple, list)) else v)
        for k, v in dict(s.attributes or {}).items()
    }
    scope = s.instrumentation_scope
    resource = s.resource
    return {
        "schema_version": SPAN_FILE_SCHEMA_VERSION,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent else None,
        "trace_flags": int(ctx.trace_flags),
        "trace_state": ctx.trace_state.to_header() if ctx.trace_state else "",
        "name": s.name,
        "kind": s.kind.name,
        "start_time_unix_nano": s.start_time or 0,
        "end_time_unix_nano": s.end_time or 0,
        "status": {
            "code": s.status.status_code.name,
            "description": s.status.description or "",
        },
        "attributes": attributes,
        "events": [
            {
                "name": e.name,
                "timestamp_unix_nano": e.timestamp,
                "attributes": dict(e.attributes or {}),
            }
            for e in s.events
        ],
        "links": [
            {
                "trace_id": format(link.context.trace_id, "032x"),
                "span_id": format(link.context.span_id, "016x"),
                "attributes": dict(link.attributes or {}),
            }
            for link in s.links
        ],
        "resource": {
            "attributes": {
                k: (list(v) if isinstance(v, (tuple, list)) else v)
                for k, v in dict(resource.attributes).items()
            },
            "schema_url": resource.schema_url or "",
        },
        "scope": {
            "name": scope.name if scope else "",
            "version": (scope.version or "") if scope else "",
            "schema_url": (scope.schema_url or "") if scope else "",
        },
        "dropped": {
            "attributes": s.dropped_attributes,
            "events": s.dropped_events,
            "links": s.dropped_links,
        },
    }


def span_sort_key(d: dict[str, Any]) -> tuple[str, int]:
    """The canonical on-disk ordering: (trace id, span creation index).

    ``creation_index`` is stamped into each row by the exporter before
    sorting. Phase 3 must order OCEL events by ``at.event.seq`` regardless —
    this key only makes the *file* order reproducible across runs.
    """
    return (str(d["trace_id"]), int(d["creation_index"]))


def canonical_line(d: dict[str, Any]) -> str:
    """Canonical JSON, one span per line."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class JSONLSpanExporter(SpanExporter):
    """Buffers every span and writes one JSONL file per session at shutdown.

    Buffering (rather than streaming) is what allows deterministic ordering:
    spans are grouped by trace, resolved to their session id via the
    ``at.session.id`` / ``session.id`` attributes, sorted with
    :func:`span_sort_key`, and written atomically via ``.tmp`` + ``os.replace``.

    Known Phase 8 consideration: the whole run is held in memory (~42 spans/
    session, so ~200k spans at the 5000-session scalability target). Sessions
    are strictly sequential, so a per-session flush keyed off
    ``begin_session`` is the natural refactor if that becomes a constraint.
    """

    def __init__(self, out_dir: Path, ids: DeterministicIdGenerator) -> None:
        self._out_dir = out_dir
        self._ids = ids
        self._spans: list[ReadableSpan] = []
        self._truncated: list[str] = []
        #: (filename, sha256) pairs, available after shutdown, for the manifest.
        self.written: list[tuple[str, str]] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        # SimpleSpanProcessor swallows exporter exceptions, so truncation is
        # recorded here and raised by the telemetry() context manager on exit.
        for span in spans:
            if span.dropped_attributes or span.dropped_events or span.dropped_links:
                self._truncated.append(span.name)
            self._spans.append(span)
        return SpanExportResult.SUCCESS

    @property
    def truncated_span_names(self) -> list[str]:
        return list(self._truncated)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def shutdown(self) -> None:
        # Reconciliation: every span id the generator issued must have been
        # exported. A started-but-never-ended span would otherwise vanish
        # without any error — the silent-drop failure mode this module exists
        # to prevent. Recorded rather than raised (SimpleSpanProcessor would
        # swallow a raise); telemetry() fails the run on it.
        issued = self._ids.issued_count()
        if len(self._spans) != issued:
            self._truncated.append(
                f"<reconciliation: {issued} span ids issued, {len(self._spans)} spans exported>"
            )
        self._out_dir.mkdir(parents=True, exist_ok=True)
        by_session: dict[str, list[dict[str, Any]]] = {}
        for span in self._spans:
            d = span_to_dict(span)
            d["creation_index"] = self._ids.creation_index(int(str(d["span_id"]), 16))
            session_id = _resolve_session_id(d["attributes"])
            if session_id is None:
                raise SpanTruncationError(
                    f"span {d['name']!r} in trace {d['trace_id']} has no resolvable "
                    "session id; refusing to write an unattributable audit span"
                )
            by_session.setdefault(session_id, []).append(d)

        self.written = []
        for session_id in sorted(by_session):
            rows = sorted(by_session[session_id], key=span_sort_key)
            filename = f"{session_id}.jsonl"
            payload = "".join(canonical_line(r) + "\n" for r in rows)
            tmp = self._out_dir / (filename + ".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
            os.replace(tmp, self._out_dir / filename)
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            self.written.append((filename, digest))


def _resolve_session_id(attributes: dict[str, Any]) -> str | None:
    for key in (SESSION_ID, OPENINFERENCE_SESSION_ID):
        value = attributes.get(key)
        if isinstance(value, str) and value:
            return value
    return None


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Everything Phase 3 needs to trust and iterate a span directory."""

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
    files: tuple[tuple[str, str], ...]  # sorted (filename, sha256)


def write_manifest(out_dir: Path, m: RunManifest) -> Path:
    """Write manifest.json canonically. Phase 3 iterates this, never listdir."""
    payload = {
        "schema_version": m.schema_version,
        "contract": m.contract,
        "run_id": m.run_id,
        "scenario_name": m.scenario_name,
        "scenario_version": m.scenario_version,
        "seed": m.seed,
        "session_count": m.session_count,
        "catalogue_sha256": m.catalogue_sha256,
        "seed_data_sha256": m.seed_data_sha256,
        "model_id": m.model_id,
        "provider": m.provider,
        "genai_semconv": m.genai_semconv,
        "files": [list(pair) for pair in sorted(m.files)],
    }
    path = out_dir / "manifest.json"
    tmp = out_dir / "manifest.json.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        fh.write("\n")
    os.replace(tmp, path)
    return path


def compute_run_id(
    seed: int,
    count: int,
    scenario_name: str,
    scenario_version: str,
    catalogue_sha256: str,
    seed_data_sha256: str,
) -> str:
    """Content-derived run id; feeds the deterministic trace/span ids.

    The catalogue and seed-data digests are part of the identity: two runs
    over materially different content must never share trace, span, or event
    ids, even when someone edits the catalogue without bumping its version.
    """
    return hashlib.sha256(
        f"{seed}|{count}|{scenario_name}|{scenario_version}|{SPAN_CONTRACT_VERSION}"
        f"|{catalogue_sha256}|{seed_data_sha256}".encode()
    ).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Telemetry:
    """The live telemetry stack handed to the session runner."""

    provider: TracerProvider
    tracer: Tracer  # plain SDK tracer for layer-A spans, deliberately not OITracer
    exporter: JSONLSpanExporter
    ids: DeterministicIdGenerator


@contextmanager
def telemetry(
    out_dir: Path,
    run_id: str,
    *,
    genai_semconv: bool = True,
    service_name: str = "auditors-trace-scenario",
) -> Iterator[Telemetry]:
    """Provision the tracer provider and OpenInference instrumentation.

    Instrumentation is a process-global monkey-patch of LangChain's callback
    manager, so it is uninstalled on exit — otherwise state leaks across tests.
    Any silently truncated span fails the run here rather than corrupting the
    log.
    """
    ids = DeterministicIdGenerator(run_id)
    exporter = JSONLSpanExporter(out_dir, ids)
    resource = Resource(
        attributes={
            "service.name": service_name,
            "service.instance.id": run_id,  # pinned: Resource.create() would uuid4
            "telemetry.sdk.language": "python",
            "telemetry.sdk.name": "opentelemetry",
        }
    )
    limits = SpanLimits(
        max_span_attributes=1024,
        max_event_attributes=1024,
        max_attributes=1024,
        max_attribute_length=None,
    )
    provider = TracerProvider(resource=resource, id_generator=ids, span_limits=limits)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentor = LangChainInstrumentor()
    instrumentor.instrument(
        tracer_provider=provider,
        config=TraceConfig(enable_genai_semconv=genai_semconv),
    )
    try:
        yield Telemetry(
            provider=provider,
            tracer=provider.get_tracer("auditors_trace.scenario"),
            exporter=exporter,
            ids=ids,
        )
    finally:
        instrumentor.uninstrument()
        provider.shutdown()
    if exporter.truncated_span_names:
        raise SpanTruncationError(
            "spans silently dropped attributes/events/links: "
            f"{sorted(set(exporter.truncated_span_names))}"
        )
