"""The T-Pot scripts a person runs look and behave alike: the logo, no raw prompts, one -h, a summary.

The single scripts have their own tests (test_scripts*.py, test_ui.py); these check all of them the same way.
"""

import os
import re
import subprocess
import unittest

from tpotctl.tests import isolate

isolate()

from tpotctl.tests import test_scripts as base  # noqa: E402

REPO = base.REPO
# the scripts with questions: the logo at their start
INTERACTIVE = ("install.sh", "update.sh", "restore.sh", "uninstall.sh", "genuser.sh", "deploy.sh",
               "docker/_builder/builder.sh")
# without questions: the small wordmark only
QUIET = ("docker/tpotinit/dist/bin/hptest.sh", "docker/tpotinit/dist/bin/attackmap_pipeline_test.sh")
# their -h is the one of the T-Pot Manager command they hand over to (tpot users add / tpot sensors add)
HANDED_OVER = ("genuser.sh", "deploy.sh")


def body(path):
    """The script without its ui block and its plain fallback: what the script itself does."""
    text = base.read(path)
    for start, end in (("# >>> tpot ui >>>", "# <<< tpot ui <<<"), ("# >>> plain fallback", "# <<< plain fallback")):
        if start in text:
            text = text[:text.index(start)] + text[text.index(end) + len(end):]
    return text


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

    def test_handed_over_help_is_the_managers(self):
        """genuser.sh / deploy.sh -h: no banner of their own, the exec to tpot shows its help."""
        for path in HANDED_OVER:
            with self.subTest(script=path):
                text = body(path)
                self.assertTrue(re.search(r'exec "\$\{?myTPOT\}?" (users|sensors) add "\$@"', text), path)


if __name__ == "__main__":
    unittest.main()
