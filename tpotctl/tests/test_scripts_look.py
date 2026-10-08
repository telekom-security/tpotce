"""The T-Pot scripts a person runs look and behave alike: the logo, no raw prompts, one -h, a summary.

The single scripts have their own tests (test_scripts*.py, test_ui.py); these check all of them the same way.
"""

import os
import re
import shlex
import shutil
import subprocess
import unittest

from tpotctl.tests import isolate

isolate()

from tpotctl.tests import test_scripts as base  # noqa: E402
from tpotctl.tests import test_ui as ui  # noqa: E402

REPO = base.REPO
# the scripts with questions: the logo at their start
INTERACTIVE = ("install.sh", "update.sh", "restore.sh", "uninstall.sh", "genuser.sh", "deploy.sh",
               "docker/_builder/builder.sh")
# without questions: the small wordmark only
QUIET = ("docker/tpotinit/dist/bin/hptest.sh", "docker/tpotinit/dist/bin/attackmap_pipeline_test.sh")
# their -h is the one of the T-Pot Manager command they hand over to (tpot users add / tpot sensors add)
HANDED_OVER = ("genuser.sh", "deploy.sh")
LOGO = "telekom security"         # the credits of the logo, only the logo has them


def body(path):
    """The script without its ui block and its plain fallback: what the script itself does."""
    text = base.read(path)
    for start, end in (("# >>> tpot ui >>>", "# <<< tpot ui <<<"), ("# >>> plain fallback", "# <<< plain fallback")):
        if start in text:
            text = text[:text.index(start)] + text[text.index(end) + len(end):]
    return text


# the calls that ask; fuYES / fuASK are the builder's wrappers on them, fuASK_VALID the one of deploy.sh
QUESTION = re.compile(r"\b(fuUI_CONFIRM|fuUI_INPUT|fuUI_CHOOSE_MANY|fuUI_CHOOSE|fuYES|fuASK_VALID|fuASK)\b"
                      r"(?!\s*\(\))")
# a question, a label or a button fits the 80 columns of a terminal with gum's frame
QUESTION_ROOM = 72
# what a ${...} or $(...) in a text stands for: a guess, the value is only known at run time
EXPANDED = "#" * 12


def shrink(text):
    """The text with every ${...} and $(...) as EXPANDED, the inner ones first."""
    text = re.sub(r"\$\{[^{}]*\}", EXPANDED, text)
    before = None
    while before != text:
        before = text
        text = re.sub(r"\$\([^()]*\)", EXPANDED, text)
    return text


def label(text):
    """A choice <label>:<value> without its value (fuUI_CHOOSE takes the last colon)."""
    return re.sub(r":(?:#{12}|[\w./-]+)$", "", text)


def question_texts(path):
    """Every literal in a call that asks (continuation lines included), the questions of restore.sh
    (the myITEMS of fuCHOOSE) and the group texts of the builder (its choice of groups)."""
    lines = body(path).split("\n")
    found = []
    for number, line in enumerate(lines):
        match = QUESTION.search(line)
        if line.lstrip().startswith("#") or not match:
            continue
        text, end = line[match.end():], number
        while lines[end].rstrip().endswith("\\"):
            end += 1
            text += " " + lines[end]
        found += [label(literal) for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', shrink(text))]
    if path == "restore.sh":
        found += [label(match.group(1)) for line in lines
                  for match in [re.search(r'&& myITEMS\+=\("([^"]*)"\)', line)] if match]
    if path.endswith("builder.sh"):
        function = re.search(r"^fuGROUP_TEXT \(\) \{\n(.*?)\n\}", body(path), re.S | re.M).group(1)
        found += [shrink(text) for text in re.findall(r'echo "([^"]*)"', function)]
    return found


def option_letters(path):
    """The letters of the script's getopts string."""
    match = re.search(r'getopts\s+"?:?([A-Za-z:]+)"?', base.read(path))
    return sorted(set(match.group(1).replace(":", ""))) if match else []


class ScriptsLookAlikeTest(base.Harness):

    def test_interactive_scripts_show_the_logo(self):
        for path in INTERACTIVE:
            text = body(path)
            with self.subTest(script=path):
                logo = re.search(r"^[^#\n]*\bmyUI_LOGO=\"?1\"?", text, re.M)
                self.assertTrue(logo, "myUI_LOGO=1")
                banner = re.search(r"^[^#\n]*\bfuUI_BANNER\b", text, re.M)          # the first call, no comment
                self.assertTrue(banner, "fuUI_BANNER")
                self.assertLess(logo.start(), banner.start())
        for path in QUIET:
            with self.subTest(script=path):
                self.assertFalse(re.search(r"myUI_LOGO=\"?1", body(path)))

    def test_no_raw_prompts(self):
        """Questions go through fuUI_* (gum or its plain fallback), not read -p or select."""
        for path in INTERACTIVE:
            with self.subTest(script=path):
                found = [line.strip() for line in body(path).splitlines()
                         if re.search(r"\bread\s+-[a-zA-Z]*[ps]", line) or re.match(r"\s*select\s", line)]
                self.assertEqual(found, [])

    def test_scripts_end_with_a_summary(self):
        for path in INTERACTIVE:
            with self.subTest(script=path):
                self.assertTrue("fuUI_SUMMARY" in body(path))

    def test_help_is_uniform(self):
        for path in INTERACTIVE:
            if path in HANDED_OVER:
                continue
            with self.subTest(script=path):
                result = self.run_script(os.path.join(REPO, path), "-h")
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, out[-600:])
                for part in ("Usage:", "Options:", "Examples:"):
                    self.assertTrue(part in out, part)
                for letter in option_letters(path):
                    self.assertTrue(re.search(r"(^|[\s,])-" + letter + r"\b", out, re.M), "-" + letter)
                wrong = self.run_script(os.path.join(REPO, path), "-Z")
                expected = 2 if path.endswith("builder.sh") else 1     # the builder: 2 is a usage error
                self.assertEqual(wrong.returncode, expected, wrong.stdout + wrong.stderr)
                self.assertTrue("### [ERROR] - " in wrong.stderr, wrong.stderr)
                self.assertFalse("Options:" in wrong.stdout + wrong.stderr)   # a hint, not the whole help

    def test_every_spinner_stops_on_ctrl_c(self):
        """fuUI_SPIN returns 130 when Ctrl+C stopped its step (gum takes the key, the step is ended):
        every call of the scripts a person runs handles that within its statement or the lines after it,
        instead of taking it for a failure and going on. The builder (a tool for releases, run as root)
        keeps its own exit codes."""
        for path in ("install.sh", "update.sh", "restore.sh", "uninstall.sh"):
            lines = body(path).split("\n")
            calls = 0
            for number, line in enumerate(lines):
                if line.lstrip().startswith("#") or not re.search(r"\bfuUI_SPIN\s+\"", line):
                    continue
                calls += 1
                end = number
                while lines[end].rstrip().endswith("\\"):
                    end += 1
                window = "\n".join(lines[number:end + 4])
                with self.subTest(script=path, line=line.strip()[:60]):
                    self.assertIn("130", window)
            with self.subTest(script=path):
                self.assertGreater(calls, 0)

    def test_help_fits_80_columns(self):
        """-h on the common 80 column SSH terminal: no line the terminal has to wrap."""
        for path in INTERACTIVE:
            if path in HANDED_OVER:
                continue
            with self.subTest(script=path):
                result = self.run_script(os.path.join(REPO, path), "-h")
                out = (result.stdout + result.stderr).replace(self.home, "/home/tpot")   # a real home
                wide = [line for line in out.splitlines() if len(line) > 80]
                self.assertEqual(wide, [])

    def test_help_never_shows_the_logo(self):
        """-h at a terminal of 120 x 49 with gum: the help, never the T-Pot logo (the decision of 8f),
        in every script that shows it for a run. genuser.sh / deploy.sh in a ~/tpotce without tpot,
        the way of their own; a run of update.sh in the same place shows it (the test can see it)."""
        stub = os.path.join(self.bin, "uname")
        with open(stub, "w", encoding="utf-8") as out:
            out.write(base.UNAME)
        os.chmod(stub, 0o755)
        for name, text in (("curl", "#!/bin/sh\nexit 7\n"), ("wget", "#!/bin/sh\nexit 4\n")):
            with open(os.path.join(self.bin, name), "w", encoding="utf-8") as out:
                out.write(text)
            os.chmod(os.path.join(self.bin, name), 0o755)
        data = os.path.join(self.home, "data")
        gum_dir = os.path.join(data, "tpotce", "bin")
        os.makedirs(gum_dir)
        ui.fake_gum(gum_dir)                     # gum 2.0.2 at the place fuUI_INIT takes it from
        tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(os.path.join(tpotce, "installer", "lib"))
        shutil.copy(os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(tpotce, "installer", "lib"))
        for name, text in (("version", "99.1.0\n"), (".env", "TPOT_TYPE=SENSOR\n")):
            with open(os.path.join(tpotce, name), "w", encoding="utf-8") as out:
                out.write(text)
        env = {"HOME": self.home, "PATH": f"{self.bin}:{os.environ['PATH']}", "XDG_DATA_HOME": data,
               "XDG_CONFIG_HOME": os.path.join(self.home, "config"), "TERM": "xterm-256color",
               "COLORTERM": "truecolor", "LANG": os.environ.get("LANG", "en_US.UTF-8")}

        def terminal(*argv):
            line = "exec bash " + " ".join(shlex.quote(a) for a in argv)
            return ui.plain(ui.at_terminal(line, env, 120, 49, source="/dev/null", timeout=30))
        self.assertEqual(terminal(os.path.join(REPO, "update.sh")).count(LOGO), 1)
        for path in INTERACTIVE:
            with self.subTest(script=path):
                out = terminal(os.path.join(REPO, path), "-h")
                self.assertNotIn(LOGO, out)
                if path not in HANDED_OVER:
                    self.assertIn("Usage:", out)

    def test_questions_fit_the_terminal(self):
        """Every question, choice and button of the scripts has at most 72 characters, so gum shows
        it on one line of an 80 column terminal. A ${...} / $(...) counts as 12: a guess (a repository
        name of the builder may be longer)."""
        for path in INTERACTIVE:
            with self.subTest(script=path):
                wide = [(len(text), text) for text in question_texts(path) if len(text) > QUESTION_ROOM]
                self.assertEqual(wide, [])
        # what it finds, and the counting itself
        for path in ("install.sh", "restore.sh", "uninstall.sh", "deploy.sh", "docker/_builder/builder.sh"):
            self.assertTrue(question_texts(path), path)
        self.assertIn("Uninstall T-Pot?", question_texts("uninstall.sh"))
        # the questions of deploy.sh's own wrapper fuASK_VALID (\b does not fire before its _)
        self.assertIn("Enter the IP/domain name of the SENSOR:", question_texts("deploy.sh"))
        self.assertIn("Enter the IPv4 address or the domain name of this HIVE:", question_texts("deploy.sh"))
        self.assertIn("Elastic Stack and Attack Map", question_texts("docker/_builder/builder.sh"))
        self.assertTrue(any(text.startswith("Import the Kibana objects") for text in question_texts("restore.sh")))
        self.assertEqual(len(label(shrink('Tag (i.e. ${myREL}-$(fuARCH "${myX}")):x'))), len("Tag (i.e. -)") + 24)
        self.assertEqual(label("Hive - all of it:h"), "Hive - all of it")
        self.assertEqual(label("Enter the name:"), "Enter the name:")

    def test_the_readme_names_every_log(self):
        """The README section The T-Pot Scripts names every log the four lifecycle scripts write
        (as ~/..., the backups folder as ~/tpot_backups), the logs of the builder and that -h shows
        no logo."""
        readme = base.read("README.md")
        section = readme[readme.index("\n## The T-Pot Scripts\n"):]
        section = section[:section.index("\n## ", 1)]
        logs = set()
        for path in ("install.sh", "update.sh", "restore.sh", "uninstall.sh"):
            for place, name in re.findall(r"(\$\{HOME\}|\$HOME|~|\$\{myBACKUPDIR\})/([\w.-]+\.log)\b", body(path)):
                logs.add(("~/tpot_backups/" if place == "${myBACKUPDIR}" else "~/") + name)
        self.assertTrue({"~/install_tpot_prepare.log", "~/install_tpot.log", "~/install_tpot_pull.log",
                         "~/uninstall_tpot.log", "~/tpot_backups/update.log", "~/tpot_backups/restore.log"} <= logs,
                        logs)
        for log in sorted(logs) + ["docker/_builder/log/"]:
            self.assertIn(f"`{log}`", section, log)
        self.assertIn("not for `-h`", section)

    def test_handed_over_help_is_the_managers(self):
        """genuser.sh / deploy.sh -h: no banner of their own, the exec to tpot shows its help."""
        for path in HANDED_OVER:
            with self.subTest(script=path):
                text = body(path)
                self.assertTrue(re.search(r'exec "\$\{?myTPOT\}?" (users|sensors) add "\$@"', text), path)


class CdpathTest(base.Harness):
    """r3-RB 5: an exported CDPATH turns `cd <relative dir>` into a search that prints the directory it
    found, so `$(cd "$(dirname "$0")" && pwd)` gives two lines and the script no longer finds its
    checkout (uninstall.sh stops, update.sh / restore.sh lose installer/lib/ui.sh, a relative -B / -c
    file names another one). The scripts clear it before their first cd."""

    SCRIPTS = ("install.sh", "update.sh", "restore.sh", "uninstall.sh", "genuser.sh", "deploy.sh")
    CDPATH = ".:/nonexistent-tpot-cdpath"

    def test_the_scripts_find_their_checkout(self):
        """Started as co/<script> from the folder above the checkout: with CDPATH the same as without."""
        work = os.path.join(self.home, "work")
        checkout = os.path.join(work, "co")
        os.makedirs(os.path.join(checkout, "installer", "lib"))
        shutil.copy(os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(checkout, "installer", "lib"))
        shutil.copy(os.path.join(REPO, "version"), checkout)
        for path in self.SCRIPTS:
            shutil.copy(os.path.join(REPO, path), checkout)
            with self.subTest(script=path):
                without = self.run_script(f"co/{path}", "-h", cwd=work)
                found = self.run_script(f"co/{path}", "-h", cwd=work, env={"CDPATH": self.CDPATH})
                self.assertEqual(without.returncode, 0, without.stdout + without.stderr)
                self.assertEqual((found.returncode, found.stdout), (without.returncode, without.stdout),
                                 found.stderr)
                self.assertNotIn("No such file", found.stderr)
                self.assertNotIn("is missing", found.stdout + found.stderr)

    def test_every_cd_comes_after_the_cdpath_is_cleared(self):
        """The bodies: `unset CDPATH` before the first cd (also those that resolve -B / -c files), the
        two checks of the tpotinit image too (they find the checkout they lie in the same way)."""
        for path in self.SCRIPTS + QUIET:
            text = body(path)
            with self.subTest(script=path):
                unset = re.search(r"^unset CDPATH$", text, re.M)
                self.assertTrue(unset)
                first = re.search(r"\bcd\s", "\n".join(line for line in text.split("\n")
                                                        if not line.lstrip().startswith("#")))
                if first:
                    code = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("#"))
                    self.assertLess(code.index("unset CDPATH"), first.start())


if __name__ == "__main__":
    unittest.main()
