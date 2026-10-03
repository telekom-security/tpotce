"""The look of tpot: Telekom magenta, T-Pot is a community project of Deutsche Telekom."""

from textual.theme import Theme

MAGENTA = "#E20074"
MAGENTA_DARK = "#A3005A"

TPOT_THEME = Theme(
    name="tpot",
    primary=MAGENTA,
    secondary=MAGENTA_DARK,
    accent=MAGENTA_DARK,
    warning="#F4B400",
    error="#E8453C",
    success="#3FA34D",
    dark=True,
)


def apply(app) -> None:
    """Register before selecting it, an unknown theme name raises."""
    app.register_theme(TPOT_THEME)
    app.theme = TPOT_THEME.name
