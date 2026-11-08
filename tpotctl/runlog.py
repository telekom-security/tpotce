"""The @@tpot marks the T-Pot scripts print for tpot (fuMARK of installer/lib/ui.sh).

`@@tpot phase <key> [title ...]` starts a phase, `@@tpot warn <key> <text ...>` is a
warning to show at the end, `@@tpot fail <key>` says that phase failed (the run goes
on) and `@@tpot changed checkout` that ~/tpotce changed, so tpot should start anew
even if the run fails later (`@@tpot changed back`: it is the one before the run again).
`@@tpot check <JSON>` carries bounded, versioned live rows, never a completed result.
installer.Progress also reads tasks, images and `@@tpot pull <JSON>`. The image-plan
and pull-output entry points filter Compose layer noise while retaining its complete
raw log; the image count is unique completed images, not bytes or an ETA.
"""

import argparse
import json
import re
import shutil
import sys
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

PREFIX = "@@tpot "


def parse_mark(line: str) -> Optional[Tuple[str, List[str]]]:
    if not line.startswith(PREFIX):
        return None
    words = line[len(PREFIX):].split()
    if not words:
        return None
    return words[0], words[1:]


class Run:
    """What a script said so far: its phases in order, the failed ones, warnings and the last lines."""

    def __init__(self, keep: int = 2000):
        self.phases: List[Tuple[str, str]] = []
        self.phase = ""
        self.warnings: List[str] = []
        self.failed: Set[str] = set()
        self.checkout_changed = False
        self.lines: deque = deque(maxlen=keep)
        self.check_id = ""
        self.check_rows: Dict[str, Dict[str, Any]] = {}
        self.check_problem = ""

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        if line == PREFIX + "check" or line.startswith(PREFIX + "check "):
            self._check_update(line[len(PREFIX + "check "):])
            return
        mark = parse_mark(line)
        if mark is None:
            self.lines.append(line)
            return
        what, words = mark
        if what == "phase" and words:
            self.phase = words[0]
            if words[0] not in (key for key, _title in self.phases):
                self.phases.append((words[0], " ".join(words[1:]) or words[0]))
        elif what == "warn" and len(words) > 1:
            self.warnings.append(" ".join(words[1:]))
        elif what == "fail" and words:
            self.failed.add(words[0])
        elif what == "changed" and words == ["checkout"]:
            self.checkout_changed = True
        elif what == "changed" and words == ["back"]:
            self.checkout_changed = False

    def _check_update(self, text: str) -> None:
        from tpotctl.checks import stages
        from tpotctl.checks.run import progress_view
        try:
            data = progress_view(text)
            if data["kind"] == "start":
                if self.check_id:
                    raise ValueError("the live run was already announced")
                self.check_id = data["run_id"]
                self.check_rows = {name: {"name": name, "stages": {
                    stage: {"state": stages.NOT_REACHED, "detail": ""} for stage in stages.STAGES}}
                                   for name in data["names"]}
            else:
                if data["run_id"] != self.check_id or data["row"]["name"] not in self.check_rows:
                    raise ValueError("the row does not belong to this live run")
                self.check_rows[data["row"]["name"]] = data["row"]
        except (ValueError, RecursionError) as error:
            self.check_problem = f"Live check update refused: {error}"
            if self.check_problem not in self.warnings:
                self.warnings.append(self.check_problem)


def pull_view(text: str) -> Dict[str, Any]:
    if len(text.encode("utf-8")) > 65536:
        raise ValueError("image progress exceeds 64 KiB")
    data = json.loads(text)
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("invalid image progress version")
    done, total, activity = data.get("done"), data.get("total"), data.get("activity")
    if type(done) is not int or done < 0 or (total is not None and (type(total) is not int or total < done)):
        raise ValueError("invalid completed image count")
    if not isinstance(activity, str) or len(activity) > 1024:
        raise ValueError("invalid image activity")
    return data


def image_plan(data: Any) -> Dict[str, str]:
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise ValueError("Compose did not provide a service/image plan")
    result = {}
    for service, config in data["services"].items():
        if not isinstance(config, dict):
            raise ValueError("Compose service has no valid configuration")
        # a service that is only built has nothing to pull, Compose skips it too
        if "image" not in config:
            continue
        image = config["image"]
        if not isinstance(image, str) or not image or any(c.isspace() or ord(c) < 32 for c in image):
            raise ValueError("Compose service has no valid image")
        result[service] = image
    return result


class ImagePull:
    """Compose progress, keyed by image rather than service or layer."""

    def __init__(self):
        self.plan: Optional[Dict[str, str]] = None
        self.ready: Set[str] = set()
        self.seen: Dict[str, Tuple[str, str]] = {}
        self.layer_phases: Set[Tuple[str, str]] = set()

    def snapshot(self, activity: str) -> Dict[str, Any]:
        if len(activity) > 1024:
            suffix = "... (full detail in pull log)"
            activity = activity[:1024 - len(suffix)] + suffix
        return {"version": 1, "done": len(self.ready),
                "total": len(set(self.plan.values())) if self.plan is not None else None,
                "activity": activity}

    def feed(self, line: str) -> Optional[Dict[str, Any]]:
        line = line.strip()
        if line.startswith(PREFIX + "image-plan "):
            mapping = json.loads(line[len(PREFIX + "image-plan "):])
            if not isinstance(mapping, dict) or self.plan is not None:
                raise ValueError("invalid or repeated image plan")
            self.plan = image_plan({"services": {name: {"image": image} for name, image in mapping.items()}})
            return self.snapshot("Starting the image pull")
        if not line:
            return None
        structured = line.startswith("{")
        parent = ""
        if structured:
            event = json.loads(line)
            if not isinstance(event, dict) or not all(isinstance(event.get(k, ""), str)
                                                     for k in ("id", "parent_id", "text", "status", "details")):
                raise ValueError("invalid Compose progress event")
            identity, text = event.get("id", ""), event.get("text", "")
            parent = event.get("parent_id", "")
            details = event.get("details", "")
            if event.get("status") == "Error":
                text, details = "Error", details or text
            if not (identity or text or details):
                raise ValueError("unrecognized Compose progress event")
        else:
            match = re.match(r"^(Image \S+|\S+)\s+(.+)$", line)
            if not match:
                return self.snapshot(line)
            identity, rest = match.groups()
            text, _, details = rest.partition(" ")
        layer = re.fullmatch(r"[a-f0-9]{12,64}", identity) is not None
        layer_phase = layer and text in ("Downloading", "Extracting")
        if layer:
            if not layer_phase and not text.startswith(("Error", "Retrying")):
                return None
            identity = parent or "Layers"
            if layer_phase:
                details = ""
        known_image = True
        if identity.startswith("Image "):
            image = identity[len("Image "):]
        elif self.plan is not None and identity in self.plan:
            image = self.plan[identity]
        elif self.plan is not None and identity in self.plan.values():
            image = identity
        else:
            if not structured and not layer:
                return self.snapshot(line)
            image, known_image = identity or "Compose", False
        if known_image and self.plan is not None and image not in self.plan.values():
            raise ValueError("progress names an image outside the Compose plan")
        if layer_phase:
            if (image, text) in self.layer_phases or image in self.ready:
                return None
            self.layer_phases.add((image, text))
        state = (text, details)
        if self.seen.get(image) == state:
            return None
        self.seen[image] = state
        if known_image and (text == "Pulled" or (text == "Skipped" and "already present locally" in details.lower())):
            self.ready.add(image)
        elif text in ("Pulling", "Error") or text.startswith("Retrying"):
            self.ready.discard(image)
            self.layer_phases = {phase for phase in self.layer_phases if phase[0] != image}
        return self.snapshot(f"{image}: {text}" + (f" - {details}" if details else ""))


def pull_output(log_path: str, marks: bool) -> None:
    progress = ImagePull()
    terminal = not marks and sys.stdout.isatty()
    print(f"Complete image-pull log: {log_path}", flush=True)
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            for line in sys.stdin:
                log.write(line)
                log.flush()
                try:
                    update = progress.feed(line)
                except (ValueError, RecursionError) as error:
                    message = f"Invalid image progress: {error}; see {log_path}"
                    if terminal:
                        print()
                    print((PREFIX + "warn pull-progress " if marks else "") + message, flush=True)
                    continue
                if update is None:
                    continue
                if marks:
                    print(PREFIX + "pull " + json.dumps(update), flush=True)
                total = update["total"]
                status = (f"{update['done']} of {total} images ready" if total is not None
                          else f"{update['done']} images ready (total unknown)")
                status += " | " + update["activity"]
                if terminal:
                    width = max(10, shutil.get_terminal_size().columns - 1)
                    print("\r" + status[:width].ljust(width), end="", flush=True)
                else:
                    print(status, flush=True)
    finally:
        # the status line ends, also on Ctrl+C: what comes next starts on a line of its own
        if terminal:
            print(flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Installer image-pull progress transport")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("image-plan")
    output = commands.add_parser("pull-output")
    output.add_argument("log")
    output.add_argument("--marks", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "image-plan":
            print(PREFIX + "image-plan " + json.dumps(image_plan(json.load(sys.stdin))))
        else:
            pull_output(args.log, args.marks)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RecursionError) as error:
        print(f"Image progress failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
