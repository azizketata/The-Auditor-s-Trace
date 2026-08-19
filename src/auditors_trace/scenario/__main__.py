"""Scenario entrypoint: ``python -m auditors_trace.scenario run --n 50 --seed 42``.

Subcommands:

- ``run``      — generate sessions and write spans + manifest
- ``seed``     — download and digest-verify the German Credit dataset only
- ``describe`` — print the span contract and catalogue (text or JSON), so the
  paper's appendix table is generated from code rather than hand-copied

Subcommand ``inject`` additionally exists (Phase 4): inject catalogue
violations into a base run, producing the clean/single/mixed splits.

Exit codes: 0 ok | 2 usage or catalogue error | 3 input unavailable |
4 span contract violation | 5 span truncation | 6 injection infeasible
(quota, donor, or base-run integrity).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from auditors_trace.model.ocel_schema import EventType, ObjectType, Qualifier
from auditors_trace.model.span_contract import (
    REQUIRED_EVENT_ATTRS,
    SPAN_CONTRACT_VERSION,
    SpanContractError,
    governance_census,
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m auditors_trace.scenario",
        description="Run the credit-scoring agent fleet and emit governance spans.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="generate sessions and write spans")
    run.add_argument("--n", type=int, required=True, help="number of sessions")
    run.add_argument("--seed", type=int, required=True, help="run seed")
    run.add_argument(
        "--out", type=Path, default=Path("data") / "generated" / "spans", help="span directory"
    )
    run.add_argument("--catalogue", type=Path, default=None, help="scenario catalogue YAML")
    run.add_argument(
        "--provider",
        choices=("scripted", "anthropic"),
        default="scripted",
        help="LLM backend; 'anthropic' needs ANTHROPIC_API_KEY (default: scripted)",
    )
    run.add_argument(
        "--genai-semconv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="also emit the derived gen_ai.* attributes on layer-B spans",
    )
    run.add_argument(
        "--seed-data",
        type=Path,
        default=None,
        help="path to a german.data-format file (skips the download)",
    )
    run.add_argument("--quiet", action="store_true", help="suppress per-session lines")

    seed = sub.add_parser("seed", help="download + digest-verify the seed dataset")
    seed.add_argument("--target", type=Path, default=Path("data") / "seed")

    describe = sub.add_parser("describe", help="print the span contract and catalogue")
    describe.add_argument("--format", choices=("text", "json"), default="text")

    inject = sub.add_parser(
        "inject", help="inject catalogue violations into a base run (three splits)"
    )
    inject.add_argument(
        "--spans",
        type=Path,
        default=Path("data") / "generated" / "spans",
        help="base span directory (reads its manifest.json)",
    )
    inject.add_argument("--seed", type=int, required=True, help="injector seed")
    inject.add_argument(
        "--out",
        type=Path,
        default=Path("data") / "generated" / "splits",
        help="output directory; one subdirectory per split",
    )
    inject.add_argument(
        "--catalogue",
        type=Path,
        default=Path("data") / "catalogue" / "violations.yaml",
        help="the pre-registered violation catalogue",
    )
    inject.add_argument(
        "--sizes",
        type=str,
        default="auto",
        help=(
            "'auto' (1:4:1 partition of the run), or explicit "
            "'clean=N,single=N,mixed=N' naming ALL THREE splits; must "
            "partition the run exactly"
        ),
    )
    inject.add_argument(
        "--distractors",
        type=int,
        default=15,
        help="near-miss distractor instances in the single split",
    )
    inject.add_argument("--quiet", action="store_true", help="suppress the summary lines")

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from auditors_trace.scenario.agents import run_fleet
    from auditors_trace.scenario.models import ProviderUnavailableError
    from auditors_trace.scenario.telemetry import SpanTruncationError

    try:
        out = run_fleet(
            args.n,
            args.seed,
            args.out,
            provider=args.provider,
            genai_semconv=args.genai_semconv,
            seed_data=args.seed_data,
            catalogue_path=args.catalogue,
            quiet=args.quiet,
        )
    except SpanContractError as exc:
        print(f"span contract violation: {exc}", file=sys.stderr)
        return 4
    except SpanTruncationError as exc:
        print(f"span truncation: {exc}", file=sys.stderr)
        return 5
    except (ProviderUnavailableError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(out / "manifest.json")
    return 0


def _cmd_inject(args: argparse.Namespace) -> int:
    import json as _json

    from auditors_trace.scenario.injector import (
        InjectionError,
        InjectionInputMissingError,
        Split,
        auto_split_sizes,
        inject_run,
        load_catalogue,
    )

    if args.distractors < 0:
        print("usage error: --distractors must be >= 0", file=sys.stderr)
        return 2

    try:
        catalogue = load_catalogue(args.catalogue)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"catalogue error: {exc}", file=sys.stderr)
        return 2

    sizes: dict[Split, int]
    if args.sizes == "auto":
        manifest_path = args.spans / "manifest.json"
        if not manifest_path.exists():
            print(f"error: no such manifest: {manifest_path}", file=sys.stderr)
            return 3
        try:
            total = int(_json.loads(manifest_path.read_text(encoding="utf-8"))["session_count"])
            sizes = auto_split_sizes(total, class_count=len(catalogue.entries))
        except (ValueError, KeyError, InjectionError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        sizes = {}
        try:
            for part in args.sizes.split(","):
                name, _, raw = part.partition("=")
                if name not in ("clean", "single", "mixed") or not raw.isdigit():
                    raise ValueError(f"bad --sizes fragment {part!r}")
                if name in sizes:
                    raise ValueError(f"duplicate split name {name!r} in --sizes")
                sizes[name] = int(raw)
            missing = {"clean", "single", "mixed"} - set(sizes)
            if missing:
                raise ValueError(f"--sizes must name all three splits; missing {sorted(missing)}")
        except ValueError as exc:
            print(f"usage error: {exc}", file=sys.stderr)
            return 2

    try:
        results = inject_run(
            args.spans,
            args.out,
            args.seed,
            catalogue,
            sizes,
            distractor_count=args.distractors,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SpanContractError as exc:
        print(f"span contract violation: {exc}", file=sys.stderr)
        return 4
    except InjectionInputMissingError as exc:
        # A distinct type, never message sniffing (Phase 3 review lesson).
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except InjectionError as exc:
        # Data-level infeasibility or integrity failure — distinct from a
        # usage error so automation can tell "fix your flags" (2) from
        # "this base run + seed cannot satisfy the catalogue" (6).
        print(f"injection infeasible: {exc}", file=sys.stderr)
        return 6
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if not args.quiet:
        for split, violations in results.items():
            counts: dict[str, int] = {}
            for violation in violations:
                counts[violation.fault_class] = counts.get(violation.fault_class, 0) + 1
            summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"{split}: {len(violations)} violations {summary}".rstrip())
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    from auditors_trace.scenario.seed import download_seed, seed_digest

    try:
        path = download_seed(args.target)
    except (OSError, ValueError) as exc:
        print(f"seed unavailable: {exc}", file=sys.stderr)
        return 3
    print(f"{path} sha256={seed_digest(path)}")
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    from auditors_trace.scenario.catalogue import default_catalogue_path, load_catalogue

    catalogue = load_catalogue(default_catalogue_path())
    if args.format == "json":
        payload = {
            "contract": SPAN_CONTRACT_VERSION,
            "object_types": [t.value for t in ObjectType],
            "event_types": [t.value for t in EventType],
            "qualifiers": [q.value for q in Qualifier],
            "required_event_attrs": {
                t.value: list(names) for t, names in REQUIRED_EVENT_ATTRS.items()
            },
            "scenario": {
                "name": catalogue.scenario_name,
                "version": catalogue.scenario_version,
                "agents": [a.role for a in catalogue.agents],
                "model": catalogue.model.model_id,
            },
        }
        print(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False))
        return 0
    print(f"span contract {SPAN_CONTRACT_VERSION}")
    print(f"object types ({len(list(ObjectType))}): {', '.join(t.value for t in ObjectType)}")
    print(f"event types ({len(list(EventType))}): {', '.join(t.value for t in EventType)}")
    print(f"qualifiers ({len(list(Qualifier))}): {', '.join(q.value for q in Qualifier)}")
    print()
    for event_type in EventType:
        print(f"{event_type.value}: {', '.join(governance_census(event_type))}")
    print()
    print(
        f"scenario {catalogue.scenario_name} v{catalogue.scenario_version} — "
        f"agents: {', '.join(a.role for a in catalogue.agents)}; "
        f"model: {catalogue.model.model_id} ({catalogue.model.provider})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code is not None else 0
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "seed":
        return _cmd_seed(args)
    if args.command == "describe":
        return _cmd_describe(args)
    if args.command == "inject":
        return _cmd_inject(args)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
