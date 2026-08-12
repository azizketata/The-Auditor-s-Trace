"""Command-line interface.

The ``check`` subcommand is what every evidence record cites as its rerun
command (section 8):

    python -m auditors_trace.cli check --log <hash> --rules <version>

An auditor must be able to run that line and get a byte-identical record back.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Dispatch a subcommand. Returns a process exit code."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
