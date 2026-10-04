"""The look of tpot, taken from the T-Pot logo (doc/t-pot_wallpaper_*.png).

T-Pot is a community project of Deutsche Telekom: Telekom magenta on black, the
petrol of the outlines and the glass of the pot, honeycomb surfaces. One theme, on
purpose: the brand colours are the look.
"""

from textual.theme import Theme

INK = "#000000"        # background, the black of the wallpaper
COMB = "#1C000E"       # surfaces, the faint honeycombs
COMB_LIT = "#38001D"   # panels, zebra stripes
WAX = "#67003A"        # inactive borders, switched off services
MAGENTA = "#E20074"    # focus, selection, your changes
PETROL = "#014463"     # outlines of the logo: information, secondary buttons
GLASS = "#ECEFF9"      # text
MIST = "#97B4D3"       # dimmed text, the shading of the glass
KEY = "#5B9CC4"        # technical names (.env keys), a lighter petrol
ASH = "#A2A2AD"        # help texts, neutral so they do not compete with keys and values

OK, WARN, ERROR = "#3FA34D", "#F4B400", "#E8453C"

# for Rich Text outside of CSS (tables, the honeycomb, the logo)
STYLE = {
    "magenta": MAGENTA, "petrol": PETROL, "glass": GLASS, "mist": MIST, "key": KEY, "ash": ASH,
    "wax": WAX, "comb": COMB_LIT, "ok": OK, "warn": WARN, "error": ERROR,
}

TPOT_THEME = Theme(
    name="tpot",
    primary=MAGENTA,
    secondary=PETROL,
    accent=MAGENTA,
    warning=WARN,
    error=ERROR,
    success=OK,
    foreground=GLASS,
    background=INK,
    surface=COMB,
    panel=COMB_LIT,
    dark=True,
    variables={
        "comb": COMB_LIT, "wax": WAX, "petrol": PETROL, "mist": MIST, "glass": GLASS, "key": KEY, "ash": ASH,
        "border": MAGENTA, "border-blurred": WAX,
        "footer-key-foreground": MAGENTA, "footer-description-foreground": MIST,
        "block-cursor-background": MAGENTA, "block-cursor-foreground": GLASS,
        "block-cursor-text-style": "bold",
        "input-selection-background": f"{MAGENTA} 40%",
        "scrollbar": WAX, "scrollbar-hover": MAGENTA, "scrollbar-active": MAGENTA,
        "scrollbar-background": INK,
    },
)


def color(name: str) -> str:
    """The colour of a token for Rich texts (tables, honeycomb, logo)."""
    return STYLE[name]


def variable_defaults() -> dict:
    """$comb, $wax, ... before a theme is applied (the CSS is parsed earlier)."""
    return dict(TPOT_THEME.variables)


def apply(app) -> None:
    """Register before selecting it, an unknown theme name raises."""
    app.register_theme(TPOT_THEME)
    app.theme = TPOT_THEME.name
