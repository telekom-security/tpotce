"""Display order and configuration changes of the shared customizer state."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import customizer_core as core
from customizer_tui import State


class CustomizerStateTest(unittest.TestCase):
    def setUp(self):
        self.catalog = core.Catalog()
        self.state = State(self.catalog, core.Selection("STANDARD"), core.DEFAULT_MAX_NETWORKS)

    def test_service_display_is_alphabetical_without_changing_catalog(self):
        order = list(self.catalog.order)
        self.assertEqual(self.state.visible("conpot"),
                         ["conpot_beckhoff_cx", "conpot_building_automation",
                          "conpot_guardian_ast", "conpot_hart_gateway", "conpot_iccp",
                          "conpot_IEC104", "conpot_ipmi", "conpot_kamstrup_382",
                          "conpot_plc_modbus"])
        self.assertEqual(self.catalog.order, order)

    def test_editions_use_the_installer_order(self):
        self.assertEqual(self.catalog.edition_names(),
                         ["STANDARD", "SENSOR", "LLM", "MINI", "MOBILE", "TARPIT"])

    def test_reverted_service_changes_are_clean(self):
        self.assertFalse(self.state.changed)
        self.state.folded.clear()
        self.assertFalse(self.state.changed)
        self.state.toggle("wordpot", False)
        self.assertTrue(self.state.changed)
        self.state.toggle("wordpot", True)
        self.assertFalse(self.state.changed)

    def test_ports_and_base_are_changes(self):
        port = self.state.original_ports("wordpot")[0]
        self.state.set_port("wordpot", port, 8088)
        self.assertTrue(self.state.changed)
        self.state.set_port("wordpot", port, None, reset=True)
        self.assertFalse(self.state.changed)
        self.state.set_selection(core.Selection("MINI"))
        self.assertTrue(self.state.changed)
        self.state.set_selection(core.Selection("STANDARD"))
        self.assertFalse(self.state.changed)
