# The Auditor's Trace
#
# Command surface is fixed by BUILD-PLAN.md section 13. Targets for phases that
# are not yet built fail loudly with the phase they need; they never no-op.

# Invariant I1: hash randomisation is a source of nondeterminism.
export PYTHONHASHSEED := 0

UV := uv run

.PHONY: check lint typecheck test scenario ingest evaluate all clean

## check: lint, typecheck, test
check: lint typecheck test

lint:
	$(UV) ruff check src tests
	$(UV) ruff format --check src tests

typecheck:
	$(UV) mypy

test:
	$(UV) pytest

# $(error) rather than `echo && exit 1`: it is expanded only when the target
# actually runs, and it involves no shell, so cmd.exe and sh behave identically.

## scenario: generate spans from the agent fleet (scripted backend by default;
## PROVIDER=anthropic requires ANTHROPIC_API_KEY)
PROVIDER ?= scripted
scenario:
	$(UV) python -m auditors_trace.scenario run --n 50 --seed 42 --provider $(PROVIDER)

## ingest: spans -> OCEL (+ C2 coverage report + span index). Consumes what
## `make scenario` wrote; defaults live in the ingest CLI.
ingest:
	$(UV) python -m auditors_trace.ingest run

## evaluate: run all systems, compute metrics
evaluate:
	$(error make evaluate requires Phase 8 -- the evaluation harness. See BUILD-PLAN.md section 9)

## all: everything, from seed data to figures
all:
	$(error make all requires Phase 8 -- the evaluation harness. See BUILD-PLAN.md section 9)

clean:
	$(UV) python -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('.mypy_cache', '.ruff_cache', '.pytest_cache')]"
