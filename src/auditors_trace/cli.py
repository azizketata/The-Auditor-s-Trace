"""Command-line interface.

The ``check`` subcommand is what every evidence record cites as its rerun
command (section 8):

    python -m auditors_trace.cli check --log <hash> --rules <version> \\
        --span-index-sha256 <hash> --crosswalk-sha256 <hash>

An auditor runs that line — supplying the artifact paths at invocation — and
gets the byte-identical evidence log back: the four values are verification
pins covering EVERY input the record bytes depend on (the OCEL content hash,
the ruleset version, the span-index sidecar content hash, and the crosswalk
file hash). Any pin mismatch refuses to render (exit 4); byte-for-byte
reproduction is also the completeness anchor for the hash chain (a truncated
bundle cannot reproduce). ``pack`` regenerates the committed study artefact
(docs/study/evidence-example.md).

Exit codes: 0 ok | 2 usage | 3 input unavailable or output unwritable |
4 verification mismatch (any pin, or a sidecar that does not belong to the
log) | 6 crosswalk or cross-validation error | 7 model, ingest, or render
rejection.
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
        "--span-index-sha256",
        default=None,
        help="expected content hash of the span-index sidecar (recommended pin)",
    )
    check.add_argument(
        "--crosswalk-sha256",
        default=None,
        help="expected sha256 of the crosswalk file bytes (recommended pin)",
    )
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
    # PID-unique temp name: a fixed name would let two concurrent runs of the
    # same rerun command truncate each other's temp file mid-write
    # (adversarial review, 19 Aug 2026). The PID never reaches output bytes.
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_output(path: Path, payload: bytes) -> int:
    """Write atomically; classify filesystem failures as exit 3 (contract)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(path, payload)
    except OSError as exc:
        print(f"error: cannot write {path}: {exc}", file=sys.stderr)
        return 3
    return 0


def _load_and_render(
    args: argparse.Namespace,
    *,
    log: OCELLog | None = None,
    ruleset: RuleSet | None = None,
) -> tuple[list[EvidenceRecord], OCELLog, str, str]:
    """Load whatever is not preloaded, render, and return
    (records, log, span_index_sha256, crosswalk_sha256).

    ``check`` passes its already-verified log and ruleset so the rendered
    bytes come from exactly the objects the pins were checked against —
    a second read would reopen the verify-then-render gap (TOCTOU;
    adversarial review, 19 Aug 2026).
    """
    from auditors_trace.constraints.engine import evaluate
    from auditors_trace.constraints.ruleset import load_ruleset
    from auditors_trace.evidence.chain import sha256_bytes
    from auditors_trace.evidence.crosswalk import load_crosswalk
    from auditors_trace.evidence.renderer import build_render_index, render_all
    from auditors_trace.ingest.mapper import read_span_index
    from auditors_trace.model.io import detect_serialisation, read_ocel
    from auditors_trace.model.log import log_hash

    if log is None:
        log = read_ocel(args.ocel, detect_serialisation(args.ocel))
    if ruleset is None:
        ruleset = load_ruleset(args.rules_file)
    index_path = args.span_index or args.ocel.with_name(args.ocel.name + ".span_index.json")
    span_index = read_span_index(index_path)
    crosswalk_sha256 = sha256_bytes(args.crosswalk.read_bytes())
    crosswalk = load_crosswalk(
        args.crosswalk, required_ids={rule.constraint_id for rule in ruleset.rules}
    )
    render_index = build_render_index(log, span_index)
    records = render_all(
        evaluate(log, ruleset),
        crosswalk=crosswalk,
        ruleset=ruleset,
        render_index=render_index,
        input_log_sha256=log_hash(log),
        crosswalk_sha256=crosswalk_sha256,
        engine_commit=getattr(args, "engine_commit", ""),
    )
    return records, log, render_index.span_index_sha256, crosswalk_sha256


def _cmd_check(args: argparse.Namespace) -> int:
    from auditors_trace.constraints.engine import EngineError
    from auditors_trace.constraints.ruleset import load_ruleset, ruleset_version
    from auditors_trace.evidence.chain import records_jsonl
    from auditors_trace.evidence.crosswalk import CrosswalkError
    from auditors_trace.evidence.renderer import RenderError, SpanIndexMismatchError
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
        # The verified log/ruleset objects are rendered directly — no re-read.
        records, _log, span_index_sha256, crosswalk_sha256 = _load_and_render(
            args, log=log, ruleset=ruleset
        )
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SpanIndexMismatchError as exc:
        print(f"verification mismatch: {exc}", file=sys.stderr)
        return 4
    except CrosswalkError as exc:
        print(f"crosswalk error: {exc}", file=sys.stderr)
        return 6
    except (IngestError, RenderError, EngineError, OCELModelError, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 7

    if args.span_index_sha256 is not None and args.span_index_sha256 != span_index_sha256:
        print(
            f"verification mismatch: span-index content hash is {span_index_sha256}, "
            f"expected {args.span_index_sha256}",
            file=sys.stderr,
        )
        return 4
    if args.crosswalk_sha256 is not None and args.crosswalk_sha256 != crosswalk_sha256:
        print(
            f"verification mismatch: crosswalk hash is {crosswalk_sha256}, "
            f"expected {args.crosswalk_sha256}",
            file=sys.stderr,
        )
        return 4

    payload = records_jsonl(records)
    if args.out is not None:
        status = _write_output(args.out, payload)
        if status != 0:
            return status
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
        records, log, _span_hash, _crosswalk_hash = _load_and_render(args)
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
    status = _write_output(args.out, markdown.encode("utf-8"))
    if status != 0:
        return status
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
