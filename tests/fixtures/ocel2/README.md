# Vendored OCEL 2.0 standard schemas

Official schema files from the OCEL 2.0 standard, used by
`tests/integration/test_ocel_validation.py` to validate our written logs
against the standard itself (not against our own code). pm4py bundles no
schema files; its validators take the schema path as an argument.

| File | Source URL | Retrieved | sha256 |
|---|---|---|---|
| `schema.json` | https://www.ocel-standard.org/2.1/ocel20-schema-json.json | 2026-08-19 | `bd3dfc26a35c5a6d49e3e4adc049aa41c1efe31576dfd564e3e399d7b04f3dc6` |
| `schema.xsd` | https://www.ocel-standard.org/2.1/ocel20-schema-xml.xsd | 2026-08-19 | `7ed3339957504a8fb9c3992d054719d4dbdf96f01443e56dda74bf9fa8c3a8b2` |

Do not edit these files; re-download from the source to update, and record
the new hashes here. Note the JSON schema types every attribute *value* as a
string — typing is carried by the `eventTypes`/`objectTypes` declarations,
which is why `model/io.py` stringifies values on write and decodes them via
the attribute-kind registry on read.
