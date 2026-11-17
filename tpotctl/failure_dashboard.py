"""Generate the T-Pot Failures saved objects (tpot-failures-*), preserving all unrelated export lines."""

import argparse
import copy
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson"
PATTERN = "tpot-failures-*"
TITLE = "T-Pot Failures"
VIEW_ID = "tpot-failures-view"
SEARCH_ID = "tpot-failures-search"
COLUMNS = ["type", "t-pot_sensor", "t-pot_log_path", "failure.stage", "failure.reason", "failure.original"]
INDEX_REF = "kibanaSavedObjectMeta.searchSourceJSON.index"


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _object(template: dict, ident: str, attributes: dict, references=()) -> dict:
    obj = {key: copy.deepcopy(template[key]) for key in ("type", "coreMigrationVersion", "typeMigrationVersion")
           if key in template}
    obj.update(id=ident, attributes=attributes, references=list(references))
    return obj


def objects(view_template: dict, search_template: dict) -> list:
    """The data view and the saved search of the failure index, in the format of the export's own objects."""
    view = _object(view_template, VIEW_ID, {
        "title": PATTERN, "name": TITLE, "timeFieldName": "@timestamp", "fields": "[]", "fieldAttrs": "{}",
        "fieldFormatMap": "{}", "sourceFilters": "[]", "runtimeFieldMap": "{}", "typeMeta": "{}",
        "allowHidden": False})
    source = {"query": {"language": "kuery", "query": ""}, "filter": [], "indexRefName": INDEX_REF}
    attrs = {"columns": list(COLUMNS), "sort": [["@timestamp", "desc"]], "version": 1,
             "kibanaSavedObjectMeta": {"searchSourceJSON": _json(source)}}
    search = _object(search_template, SEARCH_ID, {
        **attrs, "title": TITLE,
        "description": "Events Logstash could not parse or index; failure.original holds the line as read.",
        "tabs": [{"id": SEARCH_ID + "-tab", "label": "Failures", "attributes": copy.deepcopy(attrs)}],
    }, [{"id": VIEW_ID, "type": "index-pattern", "name": INDEX_REF}])
    return [view, search]


def build(export: bytes) -> bytes:
    """The export with the failure objects as generated: replaced in place, new ones before the export
    summary at the end; every other line stays byte for byte."""
    lines = export.splitlines()
    parsed = [json.loads(line) for line in lines]
    by_key = {(obj.get("type"), obj.get("id")): obj for obj in parsed}
    generated = {(obj["type"], obj["id"]): obj
                 for obj in objects(by_key["index-pattern", "logstash-*"], by_key["search", "P0f-Logs"])}
    result = []
    for obj, line in zip(parsed, lines):
        if "exportedCount" in obj:
            continue
        new = generated.pop((obj.get("type"), obj.get("id")), None)
        result.append(line if new is None or new == obj else _json(new).encode("utf-8"))
    result.extend(_json(obj).encode("utf-8") for obj in generated.values())
    for obj, line in zip(parsed, lines):
        if "exportedCount" in obj:
            result.append(line if obj["exportedCount"] == len(result) else
                          _json({**obj, "exportedCount": len(result)}).encode("utf-8"))
    return b"".join(line + b"\n" for line in result)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        before = EXPORT.read_bytes()
        updated = build(before)
        if args.check:
            with zipfile.ZipFile(str(EXPORT) + ".zip") as archive:
                current_zip = archive.read(EXPORT.name) == before
            if updated == before and current_zip:
                return 0
            print("Failure objects or export zip differ: run python3 -m tpotctl.failure_dashboard", file=sys.stderr)
            return 1
        EXPORT.write_bytes(updated)
        with zipfile.ZipFile(str(EXPORT) + ".zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(EXPORT, EXPORT.name)
        return 0
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as err:
        print(f"Cannot generate the failure objects: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
