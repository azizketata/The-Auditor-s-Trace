"""Scenario entrypoint: `python -m auditors_trace.scenario run --n 50 --seed 42`."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the agent fleet. Returns a process exit code."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
