"""Portable migration inspection and export commands (standard library only)."""

import argparse
import os
import sys
from typing import List, Optional

from tpotctl import say
from tpotctl.bootstrap import REPO_DIR
from tpotctl.migration import MigrationError, create_package, inspect_source, validate_package
from tpotctl.migration_import import default_state_dir, stage_import


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    inspect = actions.add_parser("inspect", help="read-only source checks; no package is written")
    inspect.add_argument("--source", required=True, metavar="DIR", help="the source T-Pot checkout")
    export = actions.add_parser("export", help="package supported configuration and identity data")
    export.add_argument("--source", required=True, metavar="DIR", help="the source T-Pot checkout")
    export.add_argument("--output", required=True, metavar="FILE", help="new package path; never overwritten")
    export.add_argument("--history", action="store_true", help="include Elasticsearch and Kibana; keep the source stopped")
    export.add_argument("-y", "--yes", action="store_true", help="confirm the history maintenance stop")
    export.add_argument("-B", "--become-file", metavar="FILE", help="private source sudo password file")
    resume = actions.add_parser("resume-source", help="explicitly restart a source held for history migration")
    resume.add_argument("--source", required=True, metavar="DIR", help="the original source T-Pot checkout")
    resume.add_argument("-y", "--yes", action="store_true", help="confirm the original source restart")
    resume.add_argument("-B", "--become-file", metavar="FILE", help="private source sudo password file")
    validate = actions.add_parser("validate", help="check a transferred package without extracting it")
    validate.add_argument("file", metavar="FILE", help="the migration package")
    migrate = actions.add_parser("import", help="stage a package for a fresh supported Debian installation")
    migrate.add_argument("file", metavar="FILE", help="the migration package")
    migrate.add_argument("--yes", action="store_true", help="confirm staging without an interactive prompt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tpot migrate",
        description="Inspect, export, validate or privately stage a T-Pot migration package.",
        epilog="Package integrity does not establish that its source is trusted.",
    )
    add_arguments(parser)
    return parser


def _source_summary(source: dict, file_count: int) -> str:
    elasticsearch = source["elasticsearch_version"] or "not present"
    kibana = source["kibana_version"] or "not present"
    return (
        f"Source {source['release']} ({source['role']}, {source['edition']}); "
        f"{source['os_id']} {source['os_version']}, {source['architecture']} "
        f"{source['userland_bits']}-bit; Elasticsearch {elasticsearch}; Kibana {kibana}; "
        f"{len(source['image_digests'])} local image IDs checked; {file_count} files selected."
    )


def _confirm_history(action: str, yes: bool) -> int:
    if yes:
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        say.error(f"Migration {action} needs confirmation; run it at a terminal or use --yes.")
        return 2
    if action == "export":
        say.warn("This stops the original T-Pot source and keeps it stopped after the history export.")
    else:
        say.warn("This restarts the original source and ILM. Do not run it alongside the migrated target. "
                 "A new final history export is required afterwards.")
    try:
        answer = input("Continue? [y/N] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        answer = ""
    if answer not in ("y", "yes"):
        say.warn("Migration action cancelled.")
        return 130
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.action == "inspect":
            inventory = inspect_source(args.source)
            say.ok(_source_summary(inventory.source, len(inventory.files)))
            return 0
        if args.action == "export":
            if args.history:
                confirmed = _confirm_history("export", args.yes)
                if confirmed:
                    return confirmed
                from tpotctl.migration_history import export_history
                manifest = export_history(args.source, args.output, args.become_file)
            else:
                if args.become_file:
                    parser.error("--become-file requires --history")
                inventory = inspect_source(args.source)
                manifest = create_package(args.output, inventory.source, inventory.scope, inventory.files)
            total = sum(record["size"] for record in manifest["files"])
            say.ok(f"Private migration package written to {os.path.abspath(args.output)} "
                   f"({len(manifest['files'])} files, {total} bytes).")
            if args.history:
                say.warn("The source remains stopped and guarded. Use migrate resume-source only to return to it; "
                         "that requires a new final export afterwards.")
            return 0
        if args.action == "resume-source":
            confirmed = _confirm_history("resume-source", args.yes)
            if confirmed:
                return confirmed
            from tpotctl.migration_history import resume_source
            resume_source(args.source, args.become_file)
            say.ok("The original T-Pot source and ILM are running again.")
            say.warn("Create a new final history export before completing the migration.")
            return 0
        if args.action == "import":
            if not args.yes:
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    say.error("Migration import needs confirmation; run it at a terminal or use --yes.")
                    return 2
                say.info("This stages a private package only. The installer will show the changes before applying them.")
                try:
                    answer = input("Stage this migration package? [y/N] ").strip().lower()
                except KeyboardInterrupt:
                    say.warn("Cancelled.")
                    return 130
                if answer not in ("y", "yes"):
                    say.warn("Migration import cancelled; no files were staged.")
                    return 130
            result = stage_import(args.file, REPO_DIR, default_state_dir())
            say.ok(f"Private migration package staged for {result['source_role']} "
                   f"(source T-Pot {result['source_release']}).")
            say.info("The target checkout is unchanged. Continue with install.sh --migration.")
            return 0

        manifest = validate_package(args.file)
        total = sum(record["size"] for record in manifest["files"])
        say.ok(_source_summary(manifest["source"], len(manifest["files"])))
        say.info(f"Package scopes: {', '.join(manifest['scope'])}; {total} bytes. "
                 "Integrity checked; source trust must be verified separately.")
        return 0
    except (MigrationError, OSError) as err:
        say.error(f"Migration {args.action} failed: {err}")
        return 1
    except KeyboardInterrupt:
        if args.action == "resume-source" or (args.action == "export" and args.history):
            say.warn("Migration cancelled. Any installed source guard remains; use migrate resume-source to return.")
        else:
            say.warn(f"Migration {args.action} cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
