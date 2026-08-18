"""Statlog German Credit loader (UCI dataset 144, CC BY 4.0).

Downloaded at build time into ``data/seed/``, never committed. Every byte on
disk is digest-verified before use; CITATION.txt is written next to the data
because CC BY 4.0 requires attribution at the point of distribution.

Reproducibility notes:

- ``load_applicants`` uses a seeded *hash ordering*, not ``random.sample`` —
  the sample algorithm is not a cross-version API guarantee, whereas
  ``sha256(f"{seed}:{row_index}")`` is deterministic, ``PYTHONHASHSEED``-
  independent, and version-proof. It also gives Phase 8 a property for free:
  the 50-session selection is a strict prefix of the 5000-session selection.
- ``applicant_id`` is seed-derived, so no raw row index leaks into the
  published dataset.
- A95 (female : single) has **zero rows** in the real file. The decode table
  still maps it, but no code may assume all five A9x codes appear.
- ``ground_truth_risk`` is the dataset's good/bad label. It exists for
  evaluation only and must never reach the scorecard — that would make the
  whole scenario circular.
"""

from __future__ import annotations

import hashlib
import io
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

GERMAN_CREDIT_ZIP_URL: Final[str] = (
    "https://archive.ics.uci.edu/static/public/144/statlog+german+credit+data.zip"
)
GERMAN_CREDIT_LEGACY_URL: Final[str] = (
    "https://archive.ics.uci.edu/ml/machine-learning-databases/statlog/german/german.data"
)
GERMAN_CREDIT_DATA_SHA256: Final[str] = (
    "b21f3d81db8071257d5ff1deaeba1fd4303b62712e6fcc9715c7a86202cb5871"
)

CITATION: Final[str] = (
    "Hofmann, H. (1994). Statlog (German Credit Data) [Dataset]. "
    "UCI Machine Learning Repository. https://doi.org/10.24432/C5NC77. "
    "Licensed under CC BY 4.0."
)

#: German Credit attributes 9, 13 and 20 are protected grounds under
#: non-discrimination law (NOT GDPR Art. 9 special categories; see
#: docs/PLAN-REVIEW.md A5). The scorecard must never read them; only the
#: bias-monitoring resource projects them, under AI Act Art. 10(2)(f)-(g).
SPECIAL_CATEGORY_PROXY_FIELDS: Final[tuple[str, ...]] = (
    "sex",
    "marital_status",
    "age_years",
    "foreign_worker",
)

_SEX: Final[Mapping[str, str]] = {
    "A91": "male",
    "A92": "female",
    "A93": "male",
    "A94": "male",
    "A95": "female",
}
_MARITAL: Final[Mapping[str, str]] = {
    "A91": "divorced_separated",
    "A92": "divorced_separated_married",  # not decomposable for women in this dataset
    "A93": "single",
    "A94": "married_widowed",
    "A95": "single",
}

_DECODE: Final[Mapping[str, Mapping[str, str]]] = {
    "checking_status": {
        "A11": "lt_0_dm",
        "A12": "0_to_200_dm",
        "A13": "ge_200_dm_or_salary",
        "A14": "no_checking_account",
    },
    "credit_history": {
        "A30": "no_credits_all_paid",
        "A31": "all_paid_this_bank",
        "A32": "existing_paid_duly",
        "A33": "delay_in_past",
        "A34": "critical_other_credits",
    },
    "purpose": {
        "A40": "car_new",
        "A41": "car_used",
        "A42": "furniture_equipment",
        "A43": "radio_television",
        "A44": "domestic_appliances",
        "A45": "repairs",
        "A46": "education",
        "A47": "vacation",
        "A48": "retraining",
        "A49": "business",
        "A410": "other",
    },
    "savings": {
        "A61": "lt_100_dm",
        "A62": "100_to_500_dm",
        "A63": "500_to_1000_dm",
        "A64": "ge_1000_dm",
        "A65": "unknown_or_none",
    },
    "employed_since": {
        "A71": "unemployed",
        "A72": "lt_1_year",
        "A73": "1_to_4_years",
        "A74": "4_to_7_years",
        "A75": "ge_7_years",
    },
    "other_debtors": {
        "A101": "none",
        "A102": "co_applicant",
        "A103": "guarantor",
    },
    "property_type": {
        "A121": "real_estate",
        "A122": "building_savings_or_insurance",
        "A123": "car_or_other",
        "A124": "unknown_or_none",
    },
    "other_installment_plans": {
        "A141": "bank",
        "A142": "stores",
        "A143": "none",
    },
    "housing": {
        "A151": "rent",
        "A152": "own",
        "A153": "for_free",
    },
    "job": {
        "A171": "unemployed_unskilled_nonresident",
        "A172": "unskilled_resident",
        "A173": "skilled_employee",
        "A174": "management_self_employed",
    },
    "telephone": {
        "A191": "none",
        "A192": "registered",
    },
}


@dataclass(frozen=True, slots=True)
class Applicant:
    """One decoded, pseudonymised applicant record."""

    applicant_id: str  # seed-derived pseudonym; never the row index
    row_index: int
    checking_status: str
    duration_months: int
    credit_history: str
    purpose: str
    credit_amount_dm: int
    savings: str
    employed_since: str
    installment_rate_pct: int
    # --- Art. 9 proxy block: bias monitoring only, never scoring ---
    sex: str
    marital_status: str
    age_years: int
    foreign_worker: bool
    # ---------------------------------------------------------------
    other_debtors: str
    residence_since: int
    property_type: str
    other_installment_plans: str
    housing: str
    existing_credits: int
    job: str
    dependants: int
    telephone: str
    ground_truth_risk: str  # "good" | "bad"; evaluation only, NEVER scored


def decode_tables() -> Mapping[str, Mapping[str, str]]:
    """The full A-code decode tables, plus the sex/marital projections."""
    return {**_DECODE, "sex": _SEX, "marital_status": _MARITAL}


def seed_digest(path: Path) -> str:
    """sha256 of the seed file, recorded in every run manifest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_seed(target: Path) -> Path:
    """Download the German Credit dataset if absent. Returns the data path.

    Short-circuits when the cached file already matches the pinned digest, so
    CI never re-downloads. A cached file with the *wrong* digest is rejected,
    never used.
    """
    target.mkdir(parents=True, exist_ok=True)
    data_path = target / "german.data"
    if data_path.exists():
        if seed_digest(data_path) == GERMAN_CREDIT_DATA_SHA256:
            return data_path
        raise ValueError(
            f"{data_path}: digest mismatch — cached file is corrupt or tampered; "
            "delete it and re-run"
        )

    raw: bytes | None = None
    try:
        with urllib.request.urlopen(GERMAN_CREDIT_ZIP_URL, timeout=60) as resp:
            archive = zipfile.ZipFile(io.BytesIO(resp.read()))
            raw = archive.read("german.data")
    except Exception:
        with urllib.request.urlopen(GERMAN_CREDIT_LEGACY_URL, timeout=60) as resp:
            raw = resp.read()

    digest = hashlib.sha256(raw).hexdigest()
    if digest != GERMAN_CREDIT_DATA_SHA256:
        raise ValueError(
            f"downloaded german.data digest {digest} does not match the pinned "
            f"{GERMAN_CREDIT_DATA_SHA256}"
        )
    data_path.write_bytes(raw)
    (target / "CITATION.txt").write_text(CITATION + "\n", encoding="utf-8")
    return data_path


def _decode(field: str, code: str, row_index: int) -> str:
    table = _DECODE[field]
    if code not in table:
        raise ValueError(f"row {row_index}: unknown {field} code {code!r}")
    return table[code]


def load_rows(path: Path) -> tuple[Applicant, ...]:
    """Parse every row of a german.data-format file, in file order.

    ``applicant_id`` is filled with a placeholder here; the seeded pseudonym is
    assigned by :func:`load_applicants`, which knows the run seed.
    """
    applicants: list[Applicant] = []
    for row_index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        f = line.split()
        if len(f) != 21:
            raise ValueError(f"row {row_index}: expected 21 fields, got {len(f)}")
        status_code = f[8]
        if status_code not in _SEX:
            raise ValueError(f"row {row_index}: unknown personal status code {status_code!r}")
        applicants.append(
            Applicant(
                applicant_id=f"ROW-{row_index}",
                row_index=row_index,
                checking_status=_decode("checking_status", f[0], row_index),
                duration_months=int(f[1]),
                credit_history=_decode("credit_history", f[2], row_index),
                purpose=_decode("purpose", f[3], row_index),
                credit_amount_dm=int(f[4]),
                savings=_decode("savings", f[5], row_index),
                employed_since=_decode("employed_since", f[6], row_index),
                installment_rate_pct=int(f[7]),
                sex=_SEX[status_code],
                marital_status=_MARITAL[status_code],
                age_years=int(f[12]),
                foreign_worker=f[19] == "A201",
                other_debtors=_decode("other_debtors", f[9], row_index),
                residence_since=int(f[10]),
                property_type=_decode("property_type", f[11], row_index),
                other_installment_plans=_decode("other_installment_plans", f[13], row_index),
                housing=_decode("housing", f[14], row_index),
                existing_credits=int(f[15]),
                job=_decode("job", f[16], row_index),
                dependants=int(f[17]),
                telephone=_decode("telephone", f[18], row_index),
                ground_truth_risk="good" if f[20] == "1" else "bad",
            )
        )
    if not applicants:
        raise ValueError(f"{path}: no rows parsed")
    return tuple(applicants)


def _selection_key(seed: int, row_index: int) -> str:
    return hashlib.sha256(f"{seed}:{row_index}".encode()).hexdigest()


def load_applicants(path: Path, count: int, seed: int) -> tuple[Applicant, ...]:
    """Draw a reproducible applicant sequence of length ``count``.

    The same seed yields the same order, and a shorter draw is a strict prefix
    of a longer one. Rows may repeat when ``count`` exceeds the file size
    (needed for the 5000-session scalability run over 1000 rows); repeats get
    distinct pseudonyms because the pseudonym is derived from the draw index.
    """
    rows = load_rows(path)
    ordered = sorted(rows, key=lambda r: (_selection_key(seed, r.row_index), r.row_index))
    out: list[Applicant] = []
    for draw_index in range(count):
        row = ordered[draw_index % len(ordered)]
        pseudonym = (
            "APN-"
            + hashlib.sha256(f"{seed}:{draw_index}:{row.row_index}".encode()).hexdigest()[:10]
        )
        out.append(_with_id(row, pseudonym))
    return tuple(out)


def _with_id(a: Applicant, applicant_id: str) -> Applicant:
    from dataclasses import replace

    return replace(a, applicant_id=applicant_id)


def application_id(applicant: Applicant, seed: int) -> str:
    """The stable, seed-derived application id for one applicant draw."""
    return "APP-" + hashlib.sha256(f"{seed}:{applicant.applicant_id}".encode()).hexdigest()[:10]


def demographic_projection(a: Applicant) -> dict[str, str | int | bool]:
    """The special-category payload the bias monitor retrieves — nothing else."""
    return {
        "applicant_id": a.applicant_id,
        "sex": a.sex,
        "marital_status": a.marital_status,
        "age_years": a.age_years,
        "foreign_worker": a.foreign_worker,
    }
