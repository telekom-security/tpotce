"""Generate the T-Pot Sensors saved objects, preserving all unrelated export lines."""

import argparse
import copy
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson"
PREFIX = "tpot-sensors-"
STATE_SCRIPT = """if (doc['heartbeat.received_at'].size() == 0) {
  emit('unknown');
} else {
  long age = new Date().getTime() - doc['heartbeat.received_at'].value.toInstant().toEpochMilli();
  emit(age < 180000 ? 'online' : 'offline');
}"""
VERSION_SCRIPT = """if (doc['t-pot_sensor'].size() != 0) {
  String version = doc['heartbeat.version'].size() == 0 ? '' : doc['heartbeat.version'].value;
  emit(doc['t-pot_sensor'].value + ' / ' + (version.isEmpty() ? 'unknown' : version));
}"""


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _object(template: dict, name: str, attributes: dict, references=()) -> dict:
    obj = {key: copy.deepcopy(template[key]) for key in ("type", "coreMigrationVersion", "typeMigrationVersion")
           if key in template}
    obj.update(id=PREFIX + name, attributes=attributes, references=list(references))
    return obj


def _view(template: dict, name: str, title: str, pattern: str, clock: str = "", runtime=None) -> dict:
    attrs = {"title": pattern, "name": title, "fields": "[]", "fieldAttrs": "{}", "fieldFormatMap": "{}",
             "sourceFilters": "[]", "runtimeFieldMap": _json(runtime or {}), "typeMeta": "{}", "allowHidden": False}
    if clock:
        attrs["timeFieldName"] = clock
    return _object(template, name + "-view", attrs)


def _histogram(template: dict, name: str, title: str, view: str, series: str, clock: str = "@timestamp",
               unique: str = "", description: str = "") -> dict:
    attrs = copy.deepcopy(template["attributes"])
    attrs.update(title=title, description=description)
    state = attrs["state"]
    state.update(query={"language": "kuery", "query": ""}, filters=[], internalReferences=[], adHocDataViews={})
    columns = {
        "time": {"label": "Time", "dataType": "date", "operationType": "date_histogram", "scale": "interval",
                 "isBucketed": True, "sourceField": clock,
                 "params": {"interval": "auto", "includeEmptyRows": True, "dropPartials": False}},
        "series": {"label": "Sensor / version" if name == "versions" else "Sensor", "dataType": "string",
                   "operationType": "terms", "scale": "ordinal", "isBucketed": True, "sourceField": series,
                   "params": {"size": 20, "orderBy": {"type": "column", "columnId": "value"},
                              "orderDirection": "desc", "otherBucket": False, "missingBucket": False}},
        "value": {"label": "Observed process starts" if unique else "Events" if name == "attacks" else "Samples",
                  "customLabel": True, "dataType": "number", "isBucketed": False, "scale": "ratio",
                  "operationType": "unique_count" if unique else "count",
                  "sourceField": unique or "___records___", "params": {"emptyAsNull": True}},
    }
    state["datasourceStates"] = {
        "formBased": {"layers": {"main": {"columns": columns, "columnOrder": ["time", "series", "value"],
                                         "incompleteColumns": {}, "ignoreGlobalFilters": False}}},
        "indexpattern": {"layers": {}}, "textBased": {"layers": {}},
    }
    visual = state["visualization"]
    layer = copy.deepcopy(visual["layers"][0])
    layer.update(layerId="main", accessors=["value"], xAccessor="time", splitAccessor="series",
                 yConfig=[{"forAccessor": "value", "axisMode": "left"}], seriesType="bar_stacked")
    layer.pop("splitAccessors", None)
    visual.update(layers=[layer], yLeftScale="linear", yRightScale="linear", preferredSeriesType="bar_stacked",
                  fittingFunction="None")
    visual["legend"].update(position="bottom", shouldTruncate=False)
    return _object(template, name, attrs, [
        {"id": view, "type": "index-pattern", "name": "indexpattern-datasource-layer-main"}])


def build(objects: list) -> list:
    by_key = {(obj.get("type"), obj.get("id")): obj for obj in objects}
    view_template = by_key["index-pattern", "logstash-*"]
    lens_template = by_key["lens", "96ff7109-2cbe-4b20-a621-cde6f8dd72d1"]
    search_template = by_key["search", "P0f-Logs"]
    dashboard_template = next(obj for obj in objects if obj.get("type") == "dashboard")
    markdown_template = by_key["visualization", "03aa5c40-b640-11ea-b09e-0955921226b1"]
    generated = [
        _view(view_template, "status", "T-Pot Sensors: current status", "tpot-sensor-status", runtime={
            "heartbeat.state": {"type": "keyword", "script": {"source": STATE_SCRIPT}}}),
        _view(view_template, "history", "T-Pot Sensors: heartbeat history", "tpot-sensor-heartbeat-*", "@timestamp", {
            "heartbeat.sensor_version": {"type": "keyword", "script": {"source": VERSION_SCRIPT}}}),
        _view(view_template, "starts", "T-Pot Sensors: observed process starts", "tpot-sensor-heartbeat-*",
              "heartbeat.started_at"),
    ]
    search_source = {"query": {"language": "kuery", "query": ""}, "filter": [],
                     "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index"}
    search_attrs = {
        "columns": ["t-pot_sensor", "heartbeat.state", "heartbeat.version", "heartbeat.received_at",
                    "@timestamp", "t-pot_hostname"],
        "sort": [["t-pot_sensor", "asc"]], "hideChart": True, "version": 1,
        "kibanaSavedObjectMeta": {"searchSourceJSON": _json(search_source)},
    }
    generated.append(_object(search_template, "status", {
        **search_attrs, "title": "T-Pot Sensors: current status",
        "description": "Last known heartbeat, independent of the dashboard time range.",
        "tabs": [{"id": "tpot-sensors-status-tab", "label": "Status", "attributes": copy.deepcopy(search_attrs)}],
    }, [{"id": PREFIX + "status-view", "type": "index-pattern",
         "name": "kibanaSavedObjectMeta.searchSourceJSON.index"}]))
    generated.extend([
        _histogram(lens_template, "timeline", "T-Pot Sensors: heartbeat timeline", PREFIX + "history-view",
                   "t-pot_sensor", description="Every fifth heartbeat, plotted by the sensor's clock. Top 20 sensors."),
        _histogram(lens_template, "versions", "T-Pot Sensors: versions over time", PREFIX + "history-view",
                   "heartbeat.sensor_version",
                   description="Sensor / version series by sensor time; top 20 combinations."),
        _histogram(lens_template, "starts", "T-Pot Sensors: Logstash process starts", PREFIX + "starts-view",
                   "t-pot_sensor", clock="heartbeat.started_at", unique="heartbeat.started_at",
                   description="Distinct starts, not heartbeats. Short-lived processes without a fifth heartbeat "
                               "are not in history. Top 20 sensors."),
        _histogram(lens_template, "attacks", "T-Pot Sensors: attack events", "logstash-*", "t-pot_sensor.keyword",
                   description="Attack events from logstash-*, not heartbeats. Top 20 sensors."),
    ])
    note = (
        "**Online**: the HIVE received a heartbeat less than 180 seconds ago. This confirms the Logstash "
        "connection, not honeypot health. The status table includes only sensors that have sent a heartbeat "
        "and ignores the time picker; use `tpot sensors list` for access and sensors with no heartbeat.\n\n"
        "History contains every fifth heartbeat. Timeline and versions use the sensor clock; a delivery "
        "backlog can look online while its sensor timestamps are old. Process starts use Logstash's start "
        "clock and count each observed process once. Charts show their top 20 series."
    )
    generated.append(_object(markdown_template, "about", {
        "title": "T-Pot Sensors: about", "description": "", "uiStateJSON": "{}",
        "visState": _json({"type": "markdown", "params": {
            "markdown": note, "fontSize": 12, "openLinksInNewTab": False}}),
        "kibanaSavedObjectMeta": {"searchSourceJSON": "{}"},
    }))
    panels, references = [], []
    for name, kind, x, y, w, h in (
            ("about", "visualization", 0, 0, 48, 10), ("status", "search", 0, 10, 48, 15),
            ("timeline", "lens", 0, 25, 48, 12), ("versions", "lens", 0, 37, 48, 12),
            ("starts", "lens", 0, 49, 24, 12), ("attacks", "lens", 24, 49, 24, 12)):
        reference = "panel_" + name
        panels.append({"type": kind, "panelIndex": name, "panelRefName": reference,
                       "gridData": {"x": x, "y": y, "w": w, "h": h, "i": name},
                       "embeddableConfig": {"enhancements": {}}})
        references.append({"type": kind, "id": PREFIX + name, "name": name + ":" + reference})
    attrs = copy.deepcopy(dashboard_template["attributes"])
    attrs.update(title="T-Pot Sensors",
                 description="Sensor heartbeat status, history, versions, Logstash starts and attack events.",
                 panelsJSON=_json(panels), timeRestore=True, timeFrom="now-24h", timeTo="now",
                 refreshInterval={"pause": False, "value": 30000},
                 kibanaSavedObjectMeta={"searchSourceJSON": _json({"query": {"language": "kuery", "query": ""},
                                                                  "filter": []})})
    generated.append(_object(dashboard_template, "dashboard", attrs, references))
    keys = {(obj["type"], obj["id"]): obj for obj in generated}
    result = [keys.pop((obj.get("type"), obj.get("id")), obj) for obj in objects if "exportedCount" not in obj]
    result.extend(keys.values())
    for obj in objects:
        if "exportedCount" in obj:
            result.append({**obj, "exportedCount": len(result)})
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        before = EXPORT.read_text(encoding="utf-8")
        objects = [json.loads(line) for line in before.splitlines()]
        updated = build(objects)
        if args.check:
            with zipfile.ZipFile(str(EXPORT) + ".zip") as archive:
                current_zip = archive.read(EXPORT.name) == before.encode("utf-8")
            if objects == updated and current_zip:
                return 0
            print("Sensor dashboard or export zip differs: run python3 -m tpotctl.sensor_dashboard", file=sys.stderr)
            return 1
        original = {(obj.get("type"), obj.get("id")): (obj, line)
                    for obj, line in zip(objects, before.splitlines())}
        lines = []
        for obj in updated:
            old, line = original.get((obj.get("type"), obj.get("id")), (None, None))
            lines.append(line if old == obj else _json(obj))
        EXPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with zipfile.ZipFile(str(EXPORT) + ".zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(EXPORT, EXPORT.name)
        return 0
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as err:
        print(f"Cannot generate sensor dashboard: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
