"""Test helper: the Logstash pipelines of T-Pot and their part files (docker/elk/logstash/dist).

pipelines.yml names the part files of each pipeline in its path.config, as paths in the image: the
Dockerfile copies dist/pipeline/ to /usr/share/logstash/pipeline/.
Logstash expands a path.config with Ruby's Dir.glob (braces and wildcards) and reads the files sorted by
their path, a file named twice twice; pipeline_files() does the same with the files of this checkout.

Standard library only: docker/_tests/tests/logstash_fixtures.sh runs it with the python3 of the host.
"""

import glob
import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "docker" / "elk" / "logstash" / "dist"
PIPELINE_DIR = DIST / "pipeline"
# where the Dockerfile puts PIPELINE_DIR
IMAGE_DIR = "/usr/share/logstash/pipeline/"
PIPELINES_YML = DIST / "pipelines.yml"

# the lines of a pipelines.yml this reader takes: `- key: value` starts a pipeline, `  key: value` goes on
SETTING = re.compile(r"(- |  )([A-Za-z_.]+):[ \t]*(.*?)[ \t]*")


def pipelines() -> Dict[str, Dict[str, str]]:
    """{pipeline.id: {setting: value}} of pipelines.yml."""
    path = PIPELINES_YML
    entries: List[Dict[str, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        found = SETTING.fullmatch(line)
        if not found:
            raise ValueError(f"{path.name}:{number}: no line this reader knows: {line!r}")
        lead, key, value = found.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if lead == "- ":
            entries.append({})
        elif not entries:
            raise ValueError(f"{path.name}:{number}: a setting before the first pipeline")
        if key in entries[-1]:
            raise ValueError(f"{path.name}:{number}: {key} twice")
        entries[-1][key] = value
    result: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        name = entry.get("pipeline.id")
        if not name or name in result:
            raise ValueError(f"{path.name}: a pipeline without an id or an id twice: {entry}")
        result[name] = entry
    return result


def expand_braces(pattern: str) -> List[str]:
    """The patterns of a{b,c}d as Ruby's Dir.glob expands them (abd, acd), nested braces too."""
    start = pattern.find("{")
    if start < 0:
        return [pattern]
    depth, parts, begin = 0, [], start + 1
    for position in range(start, len(pattern)):
        char = pattern[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                parts.append(pattern[begin:position])
                head, tail = pattern[:start], pattern[position + 1:]
                return [expanded for part in parts for expanded in expand_braces(head + part + tail)]
        elif char == "," and depth == 1:
            parts.append(pattern[begin:position])
            begin = position + 1
    raise ValueError(f"an unclosed brace in {pattern!r}")


def patterns(pipeline_id: str) -> List[str]:
    """The glob patterns of the path.config of a pipeline, braces expanded, as paths of this checkout."""
    config = pipelines()[pipeline_id]["path.config"]
    result = []
    for pattern in expand_braces(config):
        if not pattern.startswith(IMAGE_DIR):
            raise ValueError(f"{pipeline_id}: {pattern} is not in {IMAGE_DIR}")
        result.append(str(PIPELINE_DIR / pattern[len(IMAGE_DIR):]))
    return result


def pipeline_files(pipeline_id: str) -> List[Path]:
    """The part files Logstash reads for a pipeline, in its order (sorted by path, duplicates kept)."""
    found = [path for pattern in patterns(pipeline_id) for path in glob.glob(pattern)]
    return [Path(path) for path in sorted(found) if Path(path).is_file()]


def pipeline_text(pipeline_id: str) -> str:
    """The configuration of a pipeline as Logstash puts it together: its part files one after another."""
    return "\n".join(path.read_text(encoding="utf-8") for path in pipeline_files(pipeline_id))


def parse_file(type_name: str) -> Path:
    """The part file that parses the events of one type (the type of its file input)."""
    return PIPELINE_DIR / f"40-parse-{type_name.lower()}.conf"
