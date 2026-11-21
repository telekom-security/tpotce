"""Tests of tpotctl."""

import asyncio
import os
import shutil
import tempfile
import unittest


class AsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """IsolatedAsyncioTestCase for the Textual UI tests. Its loop runs in asyncio's debug mode, which logs
    every step that takes longer than 0.1 s; mounting a Textual screen in a test takes that long, so the
    limit is raised. The other checks of the debug mode stay."""

    SLOW_CALLBACK = 5.0

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = self.SLOW_CALLBACK
        await super().asyncSetUp()


def isolate() -> None:
    """Point XDG_CONFIG_HOME to a temporary folder (once), so an icon set a
    test chooses does not land in the config of the user running the tests."""
    if not os.environ.get("TPOT_TEST_CONFIG"):
        os.environ["TPOT_TEST_CONFIG"] = tempfile.mkdtemp(prefix="tpot-test-config-")
    os.environ["XDG_CONFIG_HOME"] = os.environ["TPOT_TEST_CONFIG"]
    os.environ.pop("TPOT_ICONS", None)
    # the look the tests check, whatever terminal runs them (ColoursTest builds the 256 one)
    os.environ.pop("TPOT_COLORS", None)
    os.environ["TEXTUAL_COLOR_SYSTEM"] = "truecolor"


def current_configuration(test, repo=None):
    """Give CLI/UI tests current generated files, keeping the real drift check inside their checkout."""
    from unittest import mock
    from tpotctl import generated, ops

    if repo is None:
        temporary = tempfile.TemporaryDirectory(prefix="tpot-config-test-")
        test.addCleanup(temporary.cleanup)
        repo = temporary.name
    source = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    shutil.copy(os.path.join(source, "env.example"), os.path.join(repo, "env.example"))
    shutil.copytree(os.path.join(source, "compose"), os.path.join(repo, "compose"), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    for name, template, kind in ((".env", "env.example", "env"),
                                  ("docker-compose.yml", "compose/standard.yml", "compose")):
        path = os.path.join(repo, name)
        if not os.path.exists(path):
            shutil.copy(os.path.join(repo, template), path)
        generated.stamp_file(path, repo, kind)
    check = ops.require_config_current
    patch = mock.patch.object(ops, "require_config_current", lambda repo_dir=repo: check(repo_dir))
    patch.start()
    test.addCleanup(patch.stop)
    return repo
