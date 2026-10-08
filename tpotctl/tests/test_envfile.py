"""envfile and the calls of re that newer Pythons deprecate (3.13: count, maxsplit and flags as
positional arguments), in every module of tpotctl and the launcher."""

import ast
import os
import tempfile
import unittest
import warnings

from tpotctl.tests import isolate

isolate()

from tpotctl import envfile  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# the positional arguments of re.<name> up to the one that must be a keyword (Python 3.13 deprecates them)
KEYWORD_ONLY_FROM = {"split": 2, "sub": 3, "subn": 3}


def sources():
    yield os.path.join(REPO, "tpot")
    for folder, _dirs, files in os.walk(os.path.join(REPO, "tpotctl")):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(folder, name)


def positional_re_calls(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
                and node.func.attr in KEYWORD_ONLY_FROM and len(node.args) > KEYWORD_ONLY_FROM[node.func.attr]):
            found.append(f"{os.path.relpath(path, REPO)}:{node.lineno} re.{node.func.attr}")
    return found


class EnvFileTest(unittest.TestCase):

    def test_an_unquoted_value_ends_at_a_comment_without_a_warning(self):
        folder = tempfile.mkdtemp(prefix="tpot-envfile-")
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, True))
        path = os.path.join(folder, ".env")
        with open(path, "w", encoding="utf-8") as out:
            out.write("TPOT_BLACKHOLE=false   # a comment\nWEB_USER='a#b'\n")
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            env = envfile.EnvFile(path)
            self.assertEqual(env.get("TPOT_BLACKHOLE"), "false")
            self.assertEqual(env.get("WEB_USER"), "a#b")

    def test_no_deprecated_positional_arguments_of_re(self):
        found = [hit for path in sources() for hit in positional_re_calls(path)]
        self.assertEqual(found, [], "give count / maxsplit / flags of re as keywords (deprecated in 3.13)")


if __name__ == "__main__":
    unittest.main()
