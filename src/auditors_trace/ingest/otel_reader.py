"""Read OTLP / OpenInference spans off disk into a span tree.

Phase 3 deliverable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Span:
    """A single OTel span. Fields land in Phase 3."""


@dataclass(frozen=True, slots=True)
class SpanTree:
    """A trace: one root span and its descendants, parent links resolved."""


def read_spans(path: Path) -> tuple[Span, ...]:
    """Read spans from an OTLP dump, sorted by (trace id, start time, span id)."""
    raise NotImplementedError


def build_span_trees(spans: tuple[Span, ...]) -> tuple[SpanTree, ...]:
    """Group spans into per-trace trees.

    Every non-root span must have a resolvable parent; an unresolvable parent is
    an error, never a silent drop.
    """
    raise NotImplementedError
