"""Command-line interface.

The ``check`` subcommand is what every evidence record cites as its rerun
command (section 8):

    python -m auditors_trace.cli check --log <hash> --rules <version>

An auditor runs that line — supplying the artifact paths at invocation — and
gets the byte-identical evidence log back: the ``--log`` and ``--rules``
values are verification pins (the recomputed log hash and the loaded ruleset
version must match them, or the command refuses to render). ``pack``
regenerates the committed study artefact (docs/study/evidence-example.md).

Exit codes: 0 ok | 2 usage | 3 input unavailable | 4 verification mismatch
(log hash or ruleset version) | 6 crosswalk or cross-validation error |
7 model, ingest, or render rejection.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auditors_trace.constraints.ruleset import RuleSet
    from auditors_trace.evidence.record import EvidenceRecord
    from auditors_trace.model.log import OCELLog


def build_parser() -> argparse.ArgumentParser:
    """The CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m auditors_trace.cli",
        description="Deterministic audit-evidence commands over a pinned OCEL log.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", help="re-derive the evidence log for a pinned (log hash, ruleset version)"
    )
    check.add_argument("--log", required=True, help="expected sha256 of the OCEL log content")
    check.add_argument("--rules", required=True, help="expected ruleset version")
    check.add_argument(
        "--ocel",
        type=Path,
        default=Path("data") / "generated" / "ocel" / "run.jsonocel",
        help="the OCEL log file to re-derive from",
    )
    check.add_argument(
        "--span-index",
        type=Path,
        default=None,
        help="span-index sidecar (default: <ocel>.span_index.json)",
    )
    check.add_argument(
        "--rules-file", type=Path, default=Path("rules") / "rules.yaml", help="the ruleset"
    )
    check.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("rules") / "crosswalk.yaml",
        help="the article/standard crosswalk (human-authored; tests pass the fixture)",
    )
    check.add_argument(
        "--engine-commit", default="", help="engine commit recorded in provenance (default '')"
    )
    check.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the evidence JSONL here (default: raw bytes to stdout)",
    )

    pack = sub.add_parser(
        "pack", help="render the study-pack markdown (one example record + session excerpt)"
    )
    pack.add_argument(
        "--ocel",
        type=Path,
        default=Path("data") / "generated" / "ocel" / "single.jsonocel",
        help="the OCEL log file to render from",
    )
    pack.add_argument("--span-index", type=Path, default=None)
    pack.add_argument("--rules-file", type=Path, default=Path("rules") / "rules.yaml")
    pack.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("rules") / "crosswalk.yaml",
        help="the crosswalk (the committed example uses the test fixture, stated in its header)",
    )
    pack.add_argument(
        "--record-index", type=int, default=0, help="which chained record to feature (default 0)"
    )
    pack.add_argument("--source-label", default="", help="provenance line for the pack header")
    pack.add_argument(
        "--out",
        type=Path,
        default=Path("docs") / "study" / "evidence-example.md",
        help="markdown output path",
    )

    return parser


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f"{path.stem}.tmp{path.suffix}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _render_records(
    args: argparse.Namespace,
) -> tuple[list[EvidenceRecord], OCELLog, str, RuleSet]:
    """The shared check/pack pipeline: returns (records, log, log hash, ruleset)."""
    from auditors_trace.constraints.engine import evaluate
    from auditors_trace.constraints.ruleset import load_ruleset
    from auditors_trace.evidence.crosswalk import load_crosswalk
    from auditors_trace.evidence.renderer import build_render_index, render_all
    from auditors_trace.ingest.mapper import read_span_index
    from auditors_trace.model.io import detect_serialisation, read_ocel
    from auditors_trace.model.log import log_hash

    log = read_ocel(args.ocel, detect_serialisation(args.ocel))
    ruleset = load_ruleset(args.rules_file)
    index_path = args.span_index or args.ocel.with_name(args.ocel.name + ".span_index.json")
    span_index = read_span_index(index_path)
    crosswalk = load_crosswalk(
        args.crosswalk, required_ids={rule.constraint_id for rule in ruleset.rules}
    )
    records = render_all(
        evaluate(log, ruleset),
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=build_render_index(log, span_index),
        input_log_sha256=log_hash(log),
        engine_commit=getattr(args, "engine_commit", ""),
    )
    return records, log, log_hash(log), ruleset


def _cmd_check(args: argparse.Namespace) -> int:
    from auditors_trace.constraints.engine import EngineError
    from auditors_trace.constraints.ruleset import load_ruleset, ruleset_version
    from auditors_trace.evidence.chain import records_jsonl
    from auditors_trace.evidence.crosswalk import CrosswalkError
    from auditors_trace.evidence.renderer import RenderError
    from auditors_trace.ingest.otel_reader import IngestError
    from auditors_trace.model.io import detect_serialisation, read_ocel
    from auditors_trace.model.log import OCELModelError, log_hash

    if not args.ocel.is_file():
        # The io layer reports a missing file as a ValueError; classify it
        # here by state, never by message (house rule).
        print(f"error: no such OCEL file: {args.ocel}", file=sys.stderr)
        return 3
    try:
        log = read_ocel(args.ocel, detect_serialisation(args.ocel))
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (OCELModelError, ValueError) as exc:
        print(f"log rejected: {exc}", file=sys.stderr)
        return 7

    actual_hash = log_hash(log)
    if actual_hash != args.log:
        print(
            f"verification mismatch: log content hash is {actual_hash}, expected {args.log}",
            file=sys.stderr,
        )
        return 4

    try:
        ruleset = load_ruleset(args.rules_file)
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"ruleset rejected: {exc}", file=sys.stderr)
        return 7
    version = ruleset_version(ruleset)
    if version != args.rules:
        print(
            f"verification mismatch: ruleset version is {version!r}, expected {args.rules!r}",
            file=sys.stderr,
        )
        return 4

    try:
        records, _log, _hash, _ruleset = _render_records(args)
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except CrosswalkError as exc:
        print(f"crosswalk error: {exc}", file=sys.stderr)
        return 6
    except (IngestError, RenderError, EngineError, OCELModelError, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 7

    payload = records_jsonl(records)
    if args.out is not None:
        _atomic_write_bytes(args.out, payload)
        print(f"{len(records)} evidence records -> {args.out}", file=sys.stderr)
    else:
        # Raw bytes, never text-mode stdout: Windows \r\n translation would
        # break byte-identical reproduction.
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        print(f"{len(records)} evidence records", file=sys.stderr)
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    from auditors_trace.constraints.engine import EngineError
    from auditors_trace.evidence.crosswalk import CrosswalkError
    from auditors_trace.evidence.pack import study_pack_markdown
    from auditors_trace.evidence.renderer import RenderError
    from auditors_trace.ingest.otel_reader import IngestError
    from auditors_trace.model.log import OCELModelError

    if not args.ocel.is_file():
        print(f"error: no such OCEL file: {args.ocel}", file=sys.stderr)
        return 3
    try:
        records, log, _hash, _ruleset = _render_records(args)
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except CrosswalkError as exc:
        print(f"crosswalk error: {exc}", file=sys.stderr)
        return 6
    except (IngestError, RenderError, EngineError, OCELModelError, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 7

    if not records:
        print("no violations rendered; nothing to pack", file=sys.stderr)
        return 7
    if not 0 <= args.record_index < len(records):
        print(
            f"usage error: --record-index {args.record_index} outside 0..{len(records) - 1}",
            file=sys.stderr,
        )
        return 2

    markdown = study_pack_markdown(records[args.record_index], log, source_label=args.source_label)
    _atomic_write_bytes(args.out, markdown.encode("utf-8"))
    print(f"study pack -> {args.out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch a subcommand. Returns a process exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code is not None else 0
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "pack":
        return _cmd_pack(args)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
