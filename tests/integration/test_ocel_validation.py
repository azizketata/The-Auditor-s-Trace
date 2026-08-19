"""Phase 1 "works in real life" evidence, part 1: third-party validation.

Nothing here trusts our own round-trip code. The bars are external: the
official OCEL 2.0 schemas from ocel-standard.org (vendored with recorded
hashes in tests/fixtures/ocel2/), and pm4py's 24-constraint relational
validator for SQLite.

On the SQLite score: our files are written through pm4py's exporter, which
emits no PK/FK DDL, so eleven constraints are structurally unsatisfiable
from here — pm4py's OWN export scores the same 13/24. The pinned split below
is therefore "everything the table layout can satisfy, satisfied; the known
pm4py DDL gaps, and nothing else, unsatisfied". A post-write DDL rebuild to
reach 24/24 is deliberately DEFERRED to Phase 9 (artifact polish): it
amounts to a second SQLite exporter and neither round-trip fidelity nor the
log hash depends on it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MINI_LOG = FIXTURES / "mini_log.json"
JSON_SCHEMA = FIXTURES / "ocel2" / "schema.json"
XSD_SCHEMA = FIXTURES / "ocel2" / "schema.xsd"

#: The constraints pm4py's own to_sql-based table layout cannot satisfy
#: (missing PK/FK DDL). Everything else must pass.
KNOWN_SQLITE_DDL_GAPS = frozenset(
    {
        "const_14_primary_key_object_event_map_type_tables",
        "const_15_primary_key_object_event_tables",
        "const_16_primary_key_event_object_table",
        "const_17_primary_key_object_object_table",
        "const_18_primary_key_event_type_spec_tables",
        "const_19_foreign_key_event",
        "const_20_foreign_key_object",
        "const_21_foreign_key_event_object",
        "const_22_foreign_key_object_object",
        "const_23_foreign_key_event_type_specific",
        "const_24_foreign_key_object_type_specific",
    }
)


def _official_json_schema() -> dict[str, Any]:
    return json.loads(JSON_SCHEMA.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_mini_log_fixture_validates_against_official_schema() -> None:
    document = json.loads(MINI_LOG.read_text(encoding="utf-8"))
    jsonschema.validate(
        document, _official_json_schema(), format_checker=jsonschema.FormatChecker()
    )


def test_written_json_validates_against_official_schema(mini_log: Any, tmp_path: Path) -> None:
    from auditors_trace.model.io import write_ocel

    path = tmp_path / "log.jsonocel"
    write_ocel(mini_log, path, "json")
    document = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(
        document, _official_json_schema(), format_checker=jsonschema.FormatChecker()
    )


def test_json_attribute_values_are_strings_as_the_standard_requires(
    mini_log: Any, tmp_path: Path
) -> None:
    """The official schema types every attribute value as a string."""
    from auditors_trace.model.io import write_ocel

    path = tmp_path / "log.jsonocel"
    write_ocel(mini_log, path, "json")
    document = json.loads(path.read_text(encoding="utf-8"))
    for event in document["events"]:
        for attribute in event.get("attributes", []):
            assert isinstance(attribute["value"], str), (event["id"], attribute)
    for obj in document["objects"]:
        for attribute in obj.get("attributes", []):
            assert isinstance(attribute["value"], str), (obj["id"], attribute)


def test_written_xml_validates_against_official_xsd(mini_log: Any, tmp_path: Path) -> None:
    """Structurally valid against the official XSD; the only tolerated
    violation class is a defect in the XSD itself.

    The XSD declares its attribute-name keys per ``object-type``/
    ``event-type`` but references them with log-global keyrefs, so any log
    in which two types share an attribute name (``role`` on Agent and
    HumanApprover; ``outcome`` on make_decision and session_end) triggers
    "More than one match found for key-sequence" — even though per-type
    attribute vocabularies are exactly how OCEL 2.0 defines attributes, and
    the official JSON schema accepts the same log without complaint. We
    assert (a) no violation outside that known class, and (b) full validity
    once the two defective keyrefs are removed from the XSD.
    """
    from lxml import etree

    from auditors_trace.model.io import write_ocel

    path = tmp_path / "log.xmlocel"
    write_ocel(mini_log, path, "xml")
    document = etree.parse(str(path))

    pristine = etree.XMLSchema(etree.parse(str(XSD_SCHEMA)))
    if not pristine.validate(document):
        for error in pristine.error_log:
            assert (
                "of keyref 'objectAttributeRef'" in error.message
                or "of keyref 'eventAttributeRef'" in error.message
            ), f"unexpected XSD violation: {error.message}"

    xsd_tree = etree.parse(str(XSD_SCHEMA))
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
    for keyref in xsd_tree.findall(".//xs:keyref", ns):
        if keyref.get("name") in ("objectAttributeRef", "eventAttributeRef"):
            parent = keyref.getparent()
            assert parent is not None
            parent.remove(keyref)
    patched = etree.XMLSchema(xsd_tree)
    assert patched.validate(document), [e.message for e in patched.error_log]


def test_sqlite_passes_every_constraint_the_layout_can_satisfy(
    mini_log: Any, tmp_path: Path
) -> None:
    from pm4py.objects.ocel.validation import ocel20_rel_validation

    from auditors_trace.model.io import write_ocel

    path = tmp_path / "log.sqlite"
    write_ocel(mini_log, path, "sqlite")
    satisfied, unsatisfied = ocel20_rel_validation.apply(str(path))
    assert set(unsatisfied) == KNOWN_SQLITE_DDL_GAPS
    assert len(set(satisfied)) == 24 - len(KNOWN_SQLITE_DDL_GAPS)


def test_names_stripping_is_injective_on_our_vocabulary() -> None:
    """pm4py's SQLite table naming mangles type names; distinct names that
    collide after stripping would silently corrupt an export. Our frozen
    vocabulary must stay injective under it."""
    from pm4py.objects.ocel.util import names_stripping

    from auditors_trace.model.ocel_schema import EventType, ObjectType

    names = [t.value for t in ObjectType] + [t.value for t in EventType]
    stripped = [names_stripping.apply(name) for name in names]
    assert len(set(stripped)) == len(names)


@pytest.mark.parametrize("schema_file", [JSON_SCHEMA, XSD_SCHEMA])
def test_vendored_schemas_match_their_recorded_hashes(schema_file: Path) -> None:
    """The vendored official schemas are byte-identical to what the README
    records — nobody edited the standard."""
    import hashlib

    readme = (FIXTURES / "ocel2" / "README.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(schema_file.read_bytes()).hexdigest()
    assert digest in readme
