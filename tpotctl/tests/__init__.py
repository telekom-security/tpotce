"""Tests of tpotctl."""

import os
import tempfile


def isolate() -> None:
    """Point XDG_CONFIG_HOME to a temporary folder (once), so an icon set a
    test chooses does not land in the config of the user running the tests."""
    if not os.environ.get("TPOT_TEST_CONFIG"):
        os.environ["TPOT_TEST_CONFIG"] = tempfile.mkdtemp(prefix="tpot-test-config-")
    os.environ["XDG_CONFIG_HOME"] = os.environ["TPOT_TEST_CONFIG"]
    os.environ.pop("TPOT_ICONS", None)
