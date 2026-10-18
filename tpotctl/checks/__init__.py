"""The checks of T-Pot (`tpot check`): every honeypot from its port to the Attack Map.

While a check runs, ewsposter sends nothing (tpotctl.checks.ewshold), and only one check runs
at a time.
"""


class CheckError(Exception):
    """A check that cannot start or go on; the text says why."""


class HoldError(CheckError):
    """ewsposter did not take up the hold: nothing was probed."""
