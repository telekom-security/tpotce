#!/usr/bin/env python3
"""Cowrie's own test suite on the patched checkout, as a gate of the image build.

The patch changes upstream code by exact needles. A needle can still match after an upstream change that
alters what the code around it does, and no needle sees that. Upstream's own tests do: every one has to pass
on the patched tree, except those in the exceptions file, each with its reason:
- "skip_modules": test modules left out (the output plugins whose packages T-Pot does not install);
- "expected_failures": tests that fail on purpose, where a persona answers as the real program does and
  upstream's test expects upstream's text; each {"reason", "expect"}, expect a text its failure has to hold
  (the persona's answer in the assertion), so a listed test that breaks in another way is no pass.

The build fails on a test that fails and is not listed, on a listed test that fails without its text, and on
a listed test that passes (the list is out of date). The suite runs in a child process on a copy of the
checkout, the copy its working folder, as `python -m unittest discover src` does upstream: what the tests
write there (downloads, tty logs, Twisted's dropin.cache) stays out of the image."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def child(out: Path) -> None:
    """Run the suite of the checkout in the working folder and write the ids of what failed."""
    sys.path.insert(0, "src")
    import cowrie  # noqa: PLC0415 - the package of the copy, not one an installation points to

    here = os.path.realpath("src")
    if not os.path.realpath(cowrie.__file__).startswith(here + os.sep):
        raise SystemExit(f"cowrie is imported from {cowrie.__file__}, not from {here}")
    skip = tuple(json.loads(os.environ["TPOT_UPSTREAM_SKIP"]))
    suite = unittest.TestLoader().discover("src", top_level_dir="src")

    def tests(node):
        for item in node:
            if isinstance(item, unittest.TestSuite):
                yield from tests(item)
            else:
                yield item

    def module_of(test) -> str:
        # a module that does not import is a test of its own, unittest.loader._FailedTest.<module>
        return test.id().removeprefix("unittest.loader._FailedTest.")

    kept = unittest.TestSuite(test for test in tests(suite)
                              if not any(module_of(test) == name or module_of(test).startswith(name + ".")
                                         for name in skip))
    with open(os.devnull, "w") as quiet:
        result = unittest.TextTestRunner(stream=quiet, verbosity=0).run(kept)
    failed = sorted({test.id() for test, _ in result.failures + result.errors}
                    | {test.id() for test in result.unexpectedSuccesses})
    out.write_text(json.dumps({"ran": result.testsRun, "failed": failed,
                               "details": {test.id(): text[-1500:] for test, text in
                                           result.failures + result.errors}}), encoding="utf-8")


def compare(found: dict, exceptions: dict) -> list[str]:
    """The problems of a run: failures not listed, listed failures without their text, listed failures that
    pass, no tests at all."""
    listed = exceptions.get("expected_failures", {})
    expected, failed, details = set(listed), set(found["failed"]), found.get("details", {})
    problems = [f"fails and is not listed: {test}\n{details.get(test, '')}" for test in sorted(failed - expected)]
    problems += [f"fails, but not as listed (without {json.dumps(listed[test]['expect'])}): {test}\n"
                 f"{details.get(test, '')}"
                 for test in sorted(failed & expected) if listed[test]["expect"] not in details.get(test, "")]
    problems += [f"listed but passes (remove it from the list): {test}" for test in sorted(expected - failed)]
    if not found.get("ran"):
        problems.append("no test ran")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cowrie-root", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return 0
    exceptions = json.loads(args.exceptions.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as folder:
        out, copy = Path(folder) / "result.json", Path(folder) / "cowrie"
        shutil.copytree(args.cowrie_root, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        env = dict(os.environ, TPOT_UPSTREAM_SKIP=json.dumps(sorted(exceptions.get("skip_modules", {}))))
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--cowrie-root", str(copy),
                        "--exceptions", str(args.exceptions.resolve()), "--child", str(out)],
                       cwd=copy, env=env, check=True)
        found = json.loads(out.read_text(encoding="utf-8"))
    problems = compare(found, exceptions)
    if problems:
        print(f"upstream tests: {len(problems)} problem(s) in {found.get('ran', 0)} tests", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"upstream tests: {found['ran']} tests, {len(found['failed'])} expected failures as listed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
