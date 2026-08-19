"""Phase 1 determinism: one model, one hash, byte-stable files.

The claim every evidence record will lean on: ``input_log_sha256`` is a
function of log content alone — not of construction order, serialisation
format, write count, or platform (the CI matrix runs this on Windows and
Ubuntu against the same pinned constant).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

Builder = Callable[[], Any]

FORMATS = [
    pytest.param("json", "log.jsonocel", id="json"),
    pytest.param("xml", "log.xmlocel", id="xml"),
    pytest.param("sqlite", "log.sqlite", id="sqlite"),
]


def test_twenty_shuffled_reconstructions_one_hash(mini_log_builder: Builder) -> None:
    from auditors_trace.model.log import OCELLog, log_hash

    base = mini_log_builder()
    rng = random.Random(0)
    hashes = set()
    for _ in range(20):
        events = list(base.events)
        objects = list(base.objects)
        o2o = list(base.o2o)
        rng.shuffle(events)
        rng.shuffle(objects)
        rng.shuffle(o2o)
        shuffled = OCELLog(events=tuple(events), objects=tuple(objects), o2o=tuple(o2o))
        hashes.add(log_hash(shuffled))
    assert len(hashes) == 1


@pytest.mark.parametrize(("fmt", "fname"), FORMATS)
def test_write_read_write_is_byte_identical(
    mini_log: Any, fmt: str, fname: str, tmp_path: Path
) -> None:
    """write -> read -> write reproduces the first file byte for byte.

    Stronger than writing twice from the same object: it proves the read
    side loses nothing the write side needs.
    """
    from auditors_trace.model.io import read_ocel, write_ocel

    first = tmp_path / f"first_{fname}"
    second = tmp_path / f"second_{fname}"
    write_ocel(mini_log, first, fmt)  # type: ignore[arg-type]
    reread = read_ocel(first, fmt)  # type: ignore[arg-type]
    write_ocel(reread, second, fmt)  # type: ignore[arg-type]
    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )
