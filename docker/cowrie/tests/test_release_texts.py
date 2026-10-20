"""The help texts that change between releases, against the recordings of fixtures/release_texts.json
(official images: busybox:1.25.1 to 1.36.1, BusyBox 1.24.1 built from its release tarball, the coreutils of
Debian 8 / 9 / 10, Ubuntu 18.04 and Alpine 3.11, the procps-ng of CentOS 7, Debian 9, Ubuntu 20.04 / 22.04).
BusyBox in its default configuration (the usage of wget, awk, sort, df; dd up to 1.28), GNU coreutils in
the C locale (wc, seq, sort and df: --help and --version), top -v of procps-ng."""
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig

DATA = json.loads((Path(__file__).parent / "fixtures" / "release_texts.json").read_text(encoding="utf-8"))


def load(source, methods, config, names=()):
    """The module-level constants and functions `names` of a patched module and the methods `methods`
    ("Class.method") as plain functions; the constants a method reads are named in `names`."""
    namespace = {"CowrieConfig": config, "re": re, "tpot_re": re}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in names or isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in names for target in node.targets):
            exec(compile(ast.Module([node], []), "<patched>", "exec"), namespace)  # noqa: S102
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and f"{node.name}.{item.name}" in methods:
                    holder = {}
                    exec(compile(ast.Module([item], []), "<patched>", "exec"), namespace, holder)  # noqa: S102
                    namespace[f"{node.name}.{item.name}"] = types.FunctionType(holder[item.name].__code__, namespace)
    missing = (set(methods) | set(names)) - set(namespace)
    if missing:
        raise AssertionError(f"missing in patched source: {sorted(missing)}")
    return namespace


def numbers(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


class ReleaseTextsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.commands = root / "src" / "cowrie" / "commands"

    def source(self, name):
        return (self.commands / name).read_text()

    def test_busybox_usage_per_release(self):
        for banner, case in DATA["busybox"].items():
            version = numbers(banner.split()[1])
            config = FakeConfig({("shell", "text_banner"): banner, ("shell", "text_version"): banner.split()[1][1:],
                                 ("shell", "wget_style"): "busybox", ("shell", "text_libc"): "glibc"})
            texts = case["texts"]
            with self.subTest(banner=banner, applet="wget"):
                ns = load(self.source("wget.py"), {"Command_tpot_wget.tpot_bb_usage"}, config,
                          ("TPOT_BB_WGET_USAGE", "tpot_bb_wget_version"))
                self.assertEqual(ns["Command_tpot_wget.tpot_bb_usage"](None), texts["wget"])
            with self.subTest(banner=banner, applet="awk"):
                ns = load(self.source("awk.py"), set(), config, ("tpot_awk_busybox_usage",))
                self.assertEqual(ns["tpot_awk_busybox_usage"](banner), texts["awk"])
            if "dd" in texts:
                with self.subTest(banner=banner, applet="dd"):
                    ns = load(self.source("dd.py"), {"Command_tpot_dd.tpot_bb_usage"}, config,
                              ("TPOT_DD_BB_USAGE", "TPOT_DD_BB_USAGE_119"))
                    me = types.SimpleNamespace(tpot_version=lambda: version)
                    self.assertEqual(ns["Command_tpot_dd.tpot_bb_usage"](me), texts["dd"])
            with self.subTest(banner=banner, applet="sort"):
                ns = load(self.source("wc.py"), {"Command_tpot_sort.tpot_busybox_usage"}, config)
                me = types.SimpleNamespace(version=version, unicode=False, tool="sort")
                head = f"{banner} multi-call binary.\n\nUsage: sort "
                self.assertEqual(head + ns["Command_tpot_sort.tpot_busybox_usage"](me), texts["sort"])
            with self.subTest(banner=banner, applet="df"):
                ns = load(self.source("fs.py"), {"Command_df.busybox_usage"}, config)
                me = types.SimpleNamespace(busybox_version=lambda: version, data={"banner": banner})
                self.assertEqual(ns["Command_df.busybox_usage"](me), texts["df"])
            with self.subTest(banner=banner, applet="ip"):
                ns = load(self.source("netstat.py"), {"Command_tpot_ip.bb_usage"}, config)
                out = []
                me = types.SimpleNamespace(style={"version": banner}, warn=out.append)
                ns["Command_tpot_ip.bb_usage"](me)
                self.assertEqual("".join(out), texts["ip"])

    def test_coreutils_help_and_version_per_release(self):
        authors = {"wc": "Paul Rubin and David MacKenzie.", "seq": "Ulrich Drepper.",
                   "sort": "Mike Haertel and Paul Eggert."}
        classes = {"wc": "Command_tpot_wc", "seq": "Command_tpot_seq", "sort": "Command_tpot_sort"}
        for version, case in DATA["coreutils"].items():
            texts = case["texts"]
            year = re.search(r"Copyright \(C\) (\d+)", texts["wc-version"]).group(1)
            config = FakeConfig({("shell", "text_version"): version, ("shell", "text_year"): year,
                                 ("shell", "text_collate"): "c", ("shell", "text_style"): "gnu"})
            methods = {f"{cls}.tpot_gnu_help" for cls in classes.values()} | {
                f"TpotTextTool.{name}" for name in ("tpot_gnu_footer", "tpot_gnu_help_lines", "tpot_gnu_version")}
            ns = load(self.source("wc.py"), methods, config, ("tpot_text",))
            for tool, cls in classes.items():
                me = types.SimpleNamespace(version=numbers(version), tool=tool, collate="c")
                me.tpot_gnu_footer = lambda me=me: ns["TpotTextTool.tpot_gnu_footer"](me)
                me.tpot_gnu_help_lines = lambda me=me: ns["TpotTextTool.tpot_gnu_help_lines"](me)
                with self.subTest(coreutils=version, tool=tool):
                    self.assertEqual(ns[f"{cls}.tpot_gnu_help"](me), texts[f"{tool}-help"])
                    self.assertEqual(ns["TpotTextTool.tpot_gnu_version"](me, authors[tool]), texts[f"{tool}-version"])
            ns = load(self.source("fs.py"), {"Command_df.gnu_help", "Command_df.gnu_help_of_release",
                                             "Command_df.gnu_version"}, config)
            me = types.SimpleNamespace(data={"coreutils": version, "coreutils_year": year, "collate": "c", "el": False})
            me.gnu_help_of_release = lambda text, me=me: ns["Command_df.gnu_help_of_release"](me, text)
            with self.subTest(coreutils=version, tool="df"):
                self.assertEqual(ns["Command_df.gnu_help"](me), texts["df-help"])
                self.assertEqual(ns["Command_df.gnu_version"](me), texts["df-version"])

    def test_top_version_per_release(self):
        source = self.source("base.py")
        cls = next(name for name in re.findall(r"^class (\w+)\(", source, re.M) if "top" in name.lower())
        ns = load(source, {f"{cls}.tpot_options", f"{cls}.tpot_flags"}, FakeConfig({}))
        for version, case in DATA["top"].items():
            out = []
            me = types.SimpleNamespace(args=["-v"], style="procps", version=numbers(version), version_text=version,
                                       write=out.append, exit=lambda *code: None)
            me.tpot_flags = lambda me=me: ns[f"{cls}.tpot_flags"](me)
            with self.subTest(procps=version):
                self.assertFalse(ns[f"{cls}.tpot_options"](me))
                self.assertEqual("".join(out), case["text"])


if __name__ == "__main__":
    unittest.main()
