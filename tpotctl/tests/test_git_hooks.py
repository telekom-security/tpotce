"""Git hook installation preserves existing user hooks."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl import git_hooks  # noqa: E402
from tpotctl import generated  # noqa: E402


class GitHooksTest(unittest.TestCase):

    def test_installs_and_refreshes_only_managed_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = os.path.join(tmp, "hooks")
            os.makedirs(hooks)
            merge = os.path.join(hooks, "post-merge")
            git_hooks.install(tmp, hooks)
            git_hooks.install(tmp, hooks)

            with open(merge, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(text.count(git_hooks.BEGIN), 1)
            self.assertEqual(text.count(git_hooks.END), 1)
            self.assertTrue(text.startswith("#!/bin/sh\n"))
            self.assertTrue(text.endswith(git_hooks.END + "\n"))
            self.assertTrue(os.path.isfile(os.path.join(hooks, "post-checkout")))
            self.assertEqual(os.stat(os.path.join(hooks, "post-checkout")).st_mode & 0o111, 0o111)

            result = subprocess.run(
                [merge], capture_output=True, text=True, env=dict(os.environ, TPOT_UPDATE_ACTIVE="1"))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_foreign_hooks_stay_byte_identical(self):
        for text in ("#!/bin/sh\necho user-hook\n", "#!/usr/bin/python3\nprint('mine')\n", "#!/bin/sh"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                hooks = os.path.join(tmp, "hooks")
                os.makedirs(hooks)
                merge = os.path.join(hooks, "post-merge")
                with open(merge, "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.chmod(merge, 0o755)
                before = os.stat(merge)
                with self.assertRaisesRegex(git_hooks.GitHookError, "left unchanged"):
                    git_hooks.install(tmp, hooks)
                with open(merge, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), text)
                self.assertEqual(os.stat(merge).st_ino, before.st_ino)
                self.assertEqual(os.stat(merge).st_mode, before.st_mode)

    def test_refuses_to_replace_a_non_executable_user_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = os.path.join(tmp, "hooks")
            os.makedirs(hooks)
            merge = os.path.join(hooks, "post-merge")
            with open(merge, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(merge, 0o644)

            with self.assertRaises(git_hooks.GitHookError):
                git_hooks.install(tmp, hooks)
            with open(merge, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "#!/bin/sh\nexit 0\n")

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_configured_shared_hooks_directory_is_not_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = os.path.join(tmp, "hooks")
            os.makedirs(hooks)
            repo = os.path.join(tmp, "repo")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "core.hooksPath", hooks], check=True)
            with self.assertRaises(git_hooks.GitHookError):
                git_hooks.install(repo)
            self.assertEqual(os.listdir(hooks), [])

    def test_manual_git_hook_warns_when_runtime_config_lags(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = os.path.join(tmp, "hooks")
            package = os.path.join(tmp, "tpotctl")
            compose_dir = os.path.join(tmp, "compose")
            os.makedirs(hooks)
            os.makedirs(package)
            os.makedirs(compose_dir)
            package_source = os.path.dirname(generated.__file__)
            for name in ("generated.py", "envfile.py"):
                shutil.copy(os.path.join(package_source, name), os.path.join(package, name))
            for name, value in (("env.example", "TPOT_VERSION=1\n"),
                                ("compose/standard.yml", "# T-Pot: STANDARD\nservices: {}\n"),
                                (".env", "TPOT_VERSION=local\n"),
                                ("docker-compose.yml", "# T-Pot: STANDARD\nservices: {}\n")):
                path = os.path.join(tmp, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(value)
            generated.stamp_file(os.path.join(tmp, ".env"), tmp, kind="env")
            generated.stamp_file(os.path.join(tmp, "docker-compose.yml"), tmp, kind="compose")
            git_hooks.install(tmp, hooks)

            hook = os.path.join(hooks, "post-merge")
            current = subprocess.run([hook], capture_output=True, text=True)
            self.assertEqual(current.returncode, 0)
            self.assertEqual(current.stderr, "")
            with open(os.path.join(tmp, "env.example"), "a", encoding="utf-8") as handle:
                handle.write("TPOT_REPO=example\n")
            stale = subprocess.run([hook], capture_output=True, text=True)
            self.assertEqual(stale.returncode, 0)
            self.assertIn(".env is out of date", stale.stderr)
            self.assertIn("Run tpot update first.", stale.stderr)
            with open(os.path.join(tmp, "compose", "standard.yml"), "a", encoding="utf-8") as handle:
                handle.write("# newer compose source\n")
            stale_compose = subprocess.run([hook], capture_output=True, text=True)
            self.assertEqual(stale_compose.returncode, 0)
            self.assertIn("docker-compose.yml is out of date", stale_compose.stderr)


if __name__ == "__main__":
    unittest.main()
