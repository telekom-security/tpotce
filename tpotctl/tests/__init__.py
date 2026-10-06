"""Tests of tpotctl."""

import os
import tempfile


def isolate() -> None:
    """Point XDG_CONFIG_HOME to a temporary folder (once), so an icon set a
    test chooses does not land in the config of the user running the tests, and
    make the host a Linux one for the TPOT_OSTYPE check (no docker call)."""
    if not os.environ.get("TPOT_TEST_CONFIG"):
        os.environ["TPOT_TEST_CONFIG"] = tempfile.mkdtemp(prefix="tpot-test-config-")
    os.environ["XDG_CONFIG_HOME"] = os.environ["TPOT_TEST_CONFIG"]
    os.environ.pop("TPOT_ICONS", None)
    # the look the tests check, whatever terminal runs them (ColoursTest builds the 256 one)
    os.environ.pop("TPOT_COLORS", None)
    os.environ["TEXTUAL_COLOR_SYSTEM"] = "truecolor"
    # the OS type of the host comes from Docker otherwise; tests that need another one set it
    os.environ["TPOT_HOST_OSTYPE"] = "linux"
