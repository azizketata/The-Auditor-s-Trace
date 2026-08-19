"""Ingest entrypoint: ``python -m auditors_trace.ingest run``.

Subcommands:

- ``run`` — map a span run (manifest-verified, or an explicit file list) to
  one OCEL 2.0 log, the C2 attribute-coverage report, and the span index
  that links OCEL events back to their OTel span ids.

Exit codes: 0 ok | 2 usage | 3 input unavailable | 4 span contract
violation | 6 ingest integrity error (hash mismatch, tree or merge
failure) | 7 OCEL model rejection. Code 5 stays reserved for the writer's
span-truncation meaning.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """The CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m auditors_trace.ingest",
        description="Map governance spans to an OCEL 2.0 log with evidence artefacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="map spans to OCEL + coverage + span index")
    source = run.add_mutually_exclusive_group()
    source.add_argument(
        "--manifest",
        type=Path,
        default=Path("data") / "generated" / "spans" / "manifest.json",
        help="run manifest; files are iterated and sha256-verified (default: %(default)s)",
    )
    source.add_argument(
        "--spans",
        type=Path,
        nargs="+",
        default=None,
        help="explicit span JSONL files, bypassing the manifest (golden fixtures)",
    )
    run.add_argument(
        "--out",
        type=Path,
        default=Path("data") / "generated" / "ocel" / "run.jsonocel",
        help="OCEL output path; serialisation inferred from the suffix",
    )
    run.add_argument(
        "--coverage",
        type=Path,
        default=Path("results") / "e2_coverage.json",
        help="attribute-coverage report path (claim C2)",
    )
    run.add_argument(
        "--span-index",
        type=Path,
        default=None,
        help="span-index path (default: <out>.span_index.json)",
    )
    run.add_argument("--quiet", action="store_true", help="suppress the summary line")
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from auditors_trace.ingest.attribute_map import coverage_json, mapped_fraction
    from auditors_trace.ingest.mapper import map_span_trees, span_index_json
    from auditors_trace.ingest.otel_reader import (
        ManifestView,
        Span,
        build_span_trees,
        read_manifest,
        read_run,
        read_spans,
    )
    from auditors_trace.model.io import detect_serialisation, write_ocel
    from auditors_trace.model.log import log_hash

    manifest: ManifestView | None = None
    if args.spans is not None:
        collected: list[Span] = []
        for path in args.spans:
            collected.extend(read_spans(path))
        spans = tuple(sorted(collected, key=lambda s: (s.trace_id, s.creation_index)))
    else:
        manifest = read_manifest(args.manifest)
        spans = read_run(args.manifest)

    run = map_span_trees(build_span_trees(spans))

    out: Path = args.out
    serialisation = detect_serialisation(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_ocel(run.log, out, serialisation)

    coverage_path: Path = args.coverage
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_path.write_text(coverage_json(run.coverage, manifest), encoding="utf-8", newline="\n")

    index_path: Path = args.span_index or out.with_name(out.name + ".span_index.json")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(span_index_json(run.span_index), encoding="utf-8", newline="\n")

    if not args.quiet:
        print(
            f"mapped {len(run.log.events)} events, {len(run.log.objects)} objects, "
            f"{len(run.log.o2o)} o2o -> {out} "
            f"(log_hash {log_hash(run.log)[:16]}..., "
            f"coverage {mapped_fraction(run.coverage):.4f})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns an exit code instead of raising SystemExit."""
    from auditors_trace.ingest.otel_reader import IngestError
    from auditors_trace.model.log import OCELModelError
    from auditors_trace.model.span_contract import SpanContractError

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        if args.command == "run":
            return _cmd_run(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SpanContractError as exc:
        print(f"span contract violation: {exc}", file=sys.stderr)
        return 4
    except IngestError as exc:
        message = str(exc)
        if "no such" in message:
            print(f"error: {message}", file=sys.stderr)
            return 3
        print(f"ingest integrity error: {message}", file=sys.stderr)
        return 6
    except OCELModelError as exc:
        print(f"ocel model rejection: {exc}", file=sys.stderr)
        return 7
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    raise AssertionError("unreachable: argparse enforces a subcommand")


if __name__ == "__main__":
    raise SystemExit(main())
