"""Command discovery follows the same task groups as the Manager pages."""

import unittest

from tpotctl import cli


class ManagerOrderTest(unittest.TestCase):
    def test_help_groups_all_commands_once(self):
        help_text = cli.build_parser().format_help()
        self.assertTrue(help_text.startswith("T-Pot Manager\n"))
        headings = ["Monitor:", "Configure:", "Access:", "Data & maintenance:"]
        positions = [help_text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        configure = help_text[positions[1]:positions[2]]
        self.assertIn("customize", configure)
        self.assertIn("env", configure)
        access = help_text[positions[2]:positions[3]]
        self.assertIn("sensors", access)
        self.assertIn("users", access)
        self.assertIn("Usage:", help_text)
        self.assertIn("Options:", help_text)

    def test_subcommand_help_preserves_options(self):
        parser = cli.build_parser()
        command = next(action for action in parser._actions if action.dest == "command")
        text = command.choices["env"].format_help()
        self.assertIn("T-Pot Manager", text)
        self.assertIn("Usage:", text)
        self.assertIn("Options:", text)
        self.assertEqual(parser.parse_args(["env", "get", "EXAMPLE"]).key, "EXAMPLE")

    def test_page_order_matches_the_grouped_help(self):
        from tpotctl.app import PANES
        self.assertEqual([key for key, *_ in PANES],
                         ["status", "checks", "images", "edition", "llm", "settings",
                          "sensors", "users", "elastic", "update"])
