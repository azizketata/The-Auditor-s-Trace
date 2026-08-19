"""Read and write OCEL 2.0 via pm4py, in all three serialisations.

Phase 1 deliverable. The Phase 1 gate — do qualified relations survive a
pm4py round trip? — PASSED on 19 Aug 2026 (docs/SPIKE-pm4py-roundtrip.md);
the spike's constraints G1-G4 are enforced by ``model/log.py``, so this
module can be a thin, honest boundary:

- pandas appears here and only here: pm4py's ``OCEL`` object *is* four
  DataFrames. Determinism never rests on pandas semantics — rows are built
  in the model's canonical order before pandas sees them on write, and the
  model re-sorts and re-validates everything after pandas on read. No pandas
  value ever enters a hash (``log_hash`` reads the model, G4).
- Attribute values are written as strings, uniformly: the official OCEL 2.0
  JSON schema types every attribute *value* as a string — typing is carried
  by the ``eventTypes``/``objectTypes`` declarations, which pm4py's
  exporters cannot emit for int/bool (they only know string/date/float), so
  ``write_ocel`` patches the JSON declarations from the attribute-kind
  registry after export, and ``read_ocel`` decodes by the same registry for
  all three formats. Encodings: bool -> "true"/"false", int -> str, float ->
  repr, tuple -> canonical JSON array string.
- The read side has no repair mode: whatever pm4py returns is decoded and
  handed to ``OCELLog``, whose validation is the gate. Invalid input raises
  ``OCELModelError``, never a silently patched log.
- ``names_stripping`` stays at pm4py's default for SQLite: our 25 type
  names are injective under it (guard-tested), and the map tables recover
  the originals.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import pandas as pd
import pm4py
from pm4py.objects.ocel.obj import OCEL

from auditors_trace.model.log import (
    JSON_SAFE_INT_MAX,
    OCELEvent,
    OCELLog,
    OCELModelError,
    OCELObject,
    OCELRelation,
    canonical_json,
)
from auditors_trace.model.ocel_schema import (
    AttributeKind,
    EventType,
    ObjectType,
    Qualifier,
    event_attribute_kinds,
    object_attribute_kinds,
)
from auditors_trace.model.span_contract import AttrValue, O2ORef

if TYPE_CHECKING:
    from collections.abc import Mapping

Serialisation = Literal["json", "xml", "sqlite"]

_E: Final = "ocel:eid"
_A: Final = "ocel:activity"
_T: Final = "ocel:timestamp"
_O: Final = "ocel:oid"
_OT: Final = "ocel:type"
_Q: Final = "ocel:qualifier"

#: Registry kind -> the OCEL 2.0 declared attribute type. STRING_LIST is
#: declared "string" because its value *is* a string (a canonical JSON
#: array); the registry, not the file, is the authority that decodes it.
_KIND_TO_OCEL_TYPE: Final[dict[AttributeKind, str]] = {
    AttributeKind.STRING: "string",
    AttributeKind.BOOLEAN: "boolean",
    AttributeKind.INTEGER: "integer",
    AttributeKind.FLOAT: "float",
    AttributeKind.STRING_LIST: "string",
}


def detect_serialisation(path: Path) -> Serialisation:
    """Map a file suffix to its serialisation, raising on anything unknown."""
    suffix = path.suffix.lower()
    if suffix in (".json", ".jsonocel"):
        return "json"
    if suffix in (".xml", ".xmlocel"):
        return "xml"
    if suffix in (".sqlite", ".sqlite3", ".db"):
        return "sqlite"
    raise OCELModelError(f"cannot infer an OCEL serialisation from suffix {suffix!r} ({path})")


# --- write ------------------------------------------------------------------


def _encode(value: AttrValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return canonical_json(list(value))


def _attr_columns(rows: list[dict[str, object]], reserved: tuple[str, ...]) -> list[str]:
    names: set[str] = set()
    for row in rows:
        names.update(k for k in row if k not in reserved)
    return sorted(names)


def _frame(rows: list[dict[str, object]], reserved: tuple[str, ...]) -> pd.DataFrame:
    columns = list(reserved) + _attr_columns(rows, reserved)
    return pd.DataFrame([{c: row.get(c) for c in columns} for row in rows], columns=columns)


def write_ocel(log: OCELLog, path: Path, serialisation: Serialisation) -> None:
    """Write the log to ``path`` in the given serialisation, deterministically."""
    path = Path(path)
    if not log.events:
        raise OCELModelError(
            "refusing to write an empty log: pm4py cannot re-read an OCEL file "
            "with no events, so the round-trip contract would be unsatisfiable"
        )
    if path.exists():
        path.unlink()

    event_rows: list[dict[str, object]] = []
    relation_rows: list[dict[str, object]] = []
    types_by_id = {obj.object_id: obj.object_type for obj in log.objects}
    for event in log.events:
        stamp = pd.Timestamp(event.timestamp)
        row: dict[str, object] = {_E: event.event_id, _A: event.event_type.value, _T: stamp}
        for name, value in event.attributes:
            row[name] = _encode(value)
        event_rows.append(row)
        for relation in event.relations:
            relation_rows.append(
                {
                    _E: event.event_id,
                    _A: event.event_type.value,
                    _T: stamp,
                    _O: relation.object_id,
                    _OT: types_by_id[relation.object_id].value,
                    _Q: relation.qualifier.value,
                }
            )

    object_rows: list[dict[str, object]] = []
    for obj in log.objects:
        object_row: dict[str, object] = {_O: obj.object_id, _OT: obj.object_type.value}
        for name, value in obj.attributes:
            object_row[name] = _encode(value)
        object_rows.append(object_row)

    o2o_rows: list[dict[str, object]] = [
        {_O: rel.source_id, _O + "_2": rel.target_id, _Q: rel.qualifier.value} for rel in log.o2o
    ]

    ocel = OCEL(
        events=_frame(event_rows, (_E, _A, _T)),
        objects=_frame(object_rows, (_O, _OT)),
        relations=pd.DataFrame(relation_rows, columns=[_E, _A, _T, _O, _OT, _Q]),
        o2o=pd.DataFrame(o2o_rows, columns=[_O, _O + "_2", _Q]),
    )

    if serialisation == "json":
        pm4py.write_ocel2_json(ocel, str(path))
        _patch_json_declared_types(path)
    elif serialisation == "xml":
        pm4py.write_ocel2_xml(ocel, str(path))
        _patch_xml_standard_shape(path, types_by_id)
    elif serialisation == "sqlite":
        pm4py.write_ocel2_sqlite(ocel, str(path))
    else:  # pragma: no cover - Literal narrows this away for typed callers
        raise OCELModelError(f"unknown serialisation {serialisation!r}")


def _patch_json_declared_types(path: Path) -> None:
    """Rewrite the JSON type declarations from the attribute-kind registry.

    pm4py's exporter can only declare string/date/float (it derives types
    from dtypes, and our value cells are uniformly strings). The registry
    knows better; with honest declarations, pm4py's own importer hands back
    typed values and any third-party consumer sees the intended types.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    for entry in doc.get("eventTypes", []):
        kinds = event_attribute_kinds(EventType(entry["name"]))
        for attr in entry.get("attributes", []):
            attr["type"] = _KIND_TO_OCEL_TYPE[kinds[attr["name"]]]
    for entry in doc.get("objectTypes", []):
        kinds = object_attribute_kinds(ObjectType(entry["name"]))
        for attr in entry.get("attributes", []):
            attr["type"] = _KIND_TO_OCEL_TYPE[kinds[attr["name"]]]
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def _patch_xml_standard_shape(path: Path, types_by_id: Mapping[str, ObjectType]) -> None:
    """Reshape pm4py's XML into the official OCEL 2.0 XSD's element forms.

    pm4py writes relation references as ``<relationship object-id=".."
    qualifier=".."/>``; the official XSD expects ``<object>`` elements with a
    mandatory ``type``. pm4py's own importer never matches the tag name — it
    reads ``object-id``/``qualifier`` off any child of ``<objects>`` — so
    the rename costs no interoperability. Declared attribute types are
    patched from the registry at the same time (the XSD enumerates
    string/time/integer/float/boolean; pm4py only ever declares string/date/
    float from dtypes).
    """
    import xml.etree.ElementTree as ElementTree

    tree = ElementTree.parse(path)
    root = tree.getroot()
    for ref in root.iter("relationship"):
        ref.tag = "object"
        target = ref.get("object-id")
        if target is not None and target in types_by_id:
            ref.set("type", types_by_id[target].value)
    for kinds_of in ("object", "event"):
        for type_entry in root.iter(f"{kinds_of}-type"):
            name = type_entry.get("name")
            if name is None:
                continue
            kinds: Mapping[str, AttributeKind] = (
                object_attribute_kinds(ObjectType(name))
                if kinds_of == "object"
                else event_attribute_kinds(EventType(name))
            )
            for attribute in type_entry.iter("attribute"):
                attr_name = attribute.get("name")
                if attr_name is not None and attr_name in kinds:
                    attribute.set("type", _KIND_TO_OCEL_TYPE[kinds[attr_name]])
    # Binary mode: a text-mode write would apply platform newline translation
    # and desynchronise file bytes between Windows and Linux.
    with path.open("wb") as sink:
        tree.write(sink, encoding="UTF-8", xml_declaration=True)


# --- read -------------------------------------------------------------------


def _is_missing(value: object) -> bool:
    return value is None or value != value


def _decode(
    owner: str,
    name: str,
    kind: AttributeKind,
    raw: object,
    serialisation: Serialisation,
) -> AttrValue:
    def fail() -> OCELModelError:
        return OCELModelError(
            f"cannot decode attribute {name!r} on {owner} as {kind.value}: {raw!r}"
        )

    if kind is AttributeKind.STRING:
        if not isinstance(raw, str):
            raise fail()
        if serialisation == "xml" and raw == "None":
            # lxml reads an empty element's text as None; pm4py stringifies
            # it. Bijective because the model bans storing "None" (R5).
            return ""
        return raw
    if kind is AttributeKind.BOOLEAN:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            return raw.strip().lower() == "true"
        raise fail()
    if kind is AttributeKind.INTEGER:
        if isinstance(raw, bool):
            raise fail()
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            if abs(raw) > JSON_SAFE_INT_MAX:
                raise OCELModelError(
                    f"attribute {name!r} on {owner} arrived as the float {raw!r}, "
                    "beyond the IEEE-754-exact bound 2**53-1: the original integer "
                    "was already truncated upstream and cannot be recovered"
                )
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                raise fail() from None
        raise fail()
    if kind is AttributeKind.FLOAT:
        if isinstance(raw, bool):
            raise fail()
        if isinstance(raw, float):
            return raw
        if isinstance(raw, int):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw)
            except ValueError:
                raise fail() from None
        raise fail()
    if kind is AttributeKind.STRING_LIST:
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                raise fail() from None
            if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
                return tuple(decoded)
        raise fail()
    raise fail()  # pragma: no cover - the enum is closed


def _decode_attrs(
    owner: str,
    record: dict[str, Any],
    kinds: Mapping[str, AttributeKind],
    reserved: tuple[str, ...],
    serialisation: Serialisation,
) -> tuple[tuple[str, AttrValue], ...]:
    pairs: list[tuple[str, AttrValue]] = []
    for name, raw in record.items():
        if name in reserved or name.startswith("ocel:") or _is_missing(raw):
            continue
        if name not in kinds:
            raise OCELModelError(
                f"{owner} carries unknown attribute {name!r}; the frozen vocabulary "
                f"allows {sorted(kinds)}"
            )
        pairs.append((name, _decode(owner, name, kinds[name], raw, serialisation)))
    return tuple(pairs)


def _canonical_timestamp(owner: str, raw: object) -> str:
    stamp = pd.Timestamp(raw) if not isinstance(raw, pd.Timestamp) else raw
    moment: datetime = stamp.to_pydatetime()
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    if moment.microsecond != 0:
        raise OCELModelError(
            f"{owner} has a sub-second timestamp {raw!r} (G3 requires whole seconds)"
        )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_enum(owner: str, cls: type[Any], raw: object) -> Any:
    try:
        return cls(raw)
    except ValueError:
        raise OCELModelError(f"{owner}: {raw!r} is not a known {cls.__name__}") from None


def read_ocel(path: Path, serialisation: Serialisation) -> OCELLog:
    """Read ``path`` into the validated model. Invalid input raises, never repairs."""
    path = Path(path)
    if not path.exists():
        raise OCELModelError(f"no such OCEL file: {path}")

    # The importer VARIANT is pinned explicitly: pm4py's top-level readers
    # silently switch to a Rust backend (rustxes) whenever it is importable,
    # which would make decode semantics depend on environment contents.
    # Anything pm4py raises while parsing is wrapped: the module's error
    # contract is OCELModelError, never a leaked pandas/lxml internal.
    try:
        if serialisation == "json":
            from pm4py.objects.ocel.importer.jsonocel import importer as json_importer

            ocel: Any = json_importer.apply(
                str(path), variant=json_importer.Variants.OCEL20_STANDARD
            )
        elif serialisation == "xml":
            from pm4py.objects.ocel.importer.xmlocel import importer as xml_importer

            ocel = xml_importer.apply(str(path), variant=xml_importer.Variants.OCEL20)
        elif serialisation == "sqlite":
            from pm4py.objects.ocel.importer.sqlite import importer as sqlite_importer

            ocel = sqlite_importer.apply(str(path), variant=sqlite_importer.Variants.OCEL20)
        else:  # pragma: no cover - Literal narrows this away for typed callers
            raise OCELModelError(f"unknown serialisation {serialisation!r}")
    except OCELModelError:
        raise
    except Exception as exc:
        raise OCELModelError(
            f"pm4py could not parse {path} as OCEL 2.0 {serialisation}: {type(exc).__name__}: {exc}"
        ) from exc

    if ocel.object_changes is not None and len(ocel.object_changes) > 0:
        raise OCELModelError(
            "the file carries dynamic object attributes (object_changes); the "
            "model does not represent them and refuses to drop them silently"
        )

    object_frame: pd.DataFrame = ocel.objects.reset_index(drop=True)
    objects: list[OCELObject] = []
    for record in object_frame.to_dict("records"):
        object_id = str(record[_O])
        object_type = _parse_enum(f"object {object_id!r}", ObjectType, record[_OT])
        objects.append(
            OCELObject(
                object_id=object_id,
                object_type=object_type,
                attributes=_decode_attrs(
                    f"object {object_id!r}",
                    record,
                    object_attribute_kinds(object_type),
                    (_O, _OT),
                    serialisation,
                ),
            )
        )

    relation_frame: pd.DataFrame = ocel.relations.reset_index(drop=True)
    relations_by_event: dict[str, list[OCELRelation]] = {}
    for record in relation_frame.to_dict("records"):
        event_id = str(record[_E])
        qualifier = _parse_enum(f"relation on event {event_id!r}", Qualifier, record[_Q])
        relations_by_event.setdefault(event_id, []).append(
            OCELRelation(object_id=str(record[_O]), qualifier=qualifier)
        )

    event_frame: pd.DataFrame = ocel.events.reset_index(drop=True)
    events: list[OCELEvent] = []
    for record in event_frame.to_dict("records"):
        event_id = str(record[_E])
        event_type = _parse_enum(f"event {event_id!r}", EventType, record[_A])
        events.append(
            OCELEvent(
                event_id=event_id,
                event_type=event_type,
                timestamp=_canonical_timestamp(f"event {event_id!r}", record[_T]),
                attributes=_decode_attrs(
                    f"event {event_id!r}",
                    record,
                    event_attribute_kinds(event_type),
                    (_E, _A, _T),
                    serialisation,
                ),
                relations=tuple(relations_by_event.get(event_id, ())),
            )
        )

    o2o_refs: list[O2ORef] = []
    if ocel.o2o is not None and len(ocel.o2o) > 0:
        o2o_frame: pd.DataFrame = ocel.o2o.reset_index(drop=True)
        for record in o2o_frame.to_dict("records"):
            qualifier = _parse_enum("o2o relation", Qualifier, record[_Q])
            o2o_refs.append(
                O2ORef(
                    source_id=str(record[_O]),
                    target_id=str(record[_O + "_2"]),
                    qualifier=qualifier,
                )
            )

    return OCELLog(events=tuple(events), objects=tuple(objects), o2o=tuple(o2o_refs))
