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

TRUECOLOR = {"INK": INK, "COMB": COMB, "COMB_LIT": COMB_LIT, "WAX": WAX, "MAGENTA": MAGENTA, "PETROL": PETROL,
             "GLASS": GLASS, "MIST": MIST, "KEY": KEY, "ASH": ASH, "OK": OK, "WARN": WARN, "ERROR": ERROR}
# The same tokens as entries of the xterm 256 palette, for a terminal that only says 256 colours
# (SSH without COLORTERM, tmux): left to the nearest entry, the dark magentas turn into maroon.
PALETTE_256 = {"INK": "#000000", "COMB": "#121212", "COMB_LIT": "#5f005f", "WAX": "#87005f",
               "MAGENTA": "#d70087", "PETROL": "#005f5f", "GLASS": "#ffffff", "MIST": "#87afd7",
               "KEY": "#5fafd7", "ASH": "#a8a8a8", "OK": "#5faf5f", "WARN": "#ffaf00", "ERROR": "#d75f5f"}


def build(system: str) -> Theme:
    """STYLE and TPOT_THEME from the true colours or, for "256", from PALETTE_256."""
    global STYLE, TPOT_THEME
    p = PALETTE_256 if system == "256" else TRUECOLOR
    # for Rich Text outside of CSS (tables, the honeycomb, the logo)
    STYLE = {
        "magenta": p["MAGENTA"], "petrol": p["PETROL"], "glass": p["GLASS"], "mist": p["MIST"], "key": p["KEY"],
        "ash": p["ASH"], "wax": p["WAX"], "comb": p["COMB_LIT"], "ok": p["OK"], "warn": p["WARN"],
        "error": p["ERROR"],
    }
    TPOT_THEME = Theme(
        name="tpot",
        primary=p["MAGENTA"],
        secondary=p["PETROL"],
        accent=p["MAGENTA"],
        warning=p["WARN"],
        error=p["ERROR"],
        success=p["OK"],
        foreground=p["GLASS"],
        background=p["INK"],
        surface=p["COMB"],
        panel=p["COMB_LIT"],
        dark=True,
        variables={
            "comb": p["COMB_LIT"], "wax": p["WAX"], "petrol": p["PETROL"], "mist": p["MIST"], "glass": p["GLASS"],
            "key": p["KEY"], "ash": p["ASH"],
            "border": p["MAGENTA"], "border-blurred": p["WAX"],
            "footer-key-foreground": p["MAGENTA"], "footer-description-foreground": p["MIST"],
            "block-cursor-background": p["MAGENTA"], "block-cursor-foreground": p["GLASS"],
            "block-cursor-text-style": "bold",
            "input-selection-background": f"{p['MAGENTA']} 40%",
            "scrollbar": p["WAX"], "scrollbar-hover": p["MAGENTA"], "scrollbar-active": p["MAGENTA"],
            "scrollbar-background": p["INK"],
            # the background of a focused field; magenta at 35 % turns into maroon with 256 colours
            "focus-tint": f"{p['MAGENTA']} 35%" if system != "256" else p["WAX"],
            # what a dialog leaves of the page below it: dimmed further, magenta goes red with 256
            "modal-dim": f"{p['INK']} 70%" if system != "256" else f"{p['INK']} 40%",
            **({"block-cursor-blurred-background": p["COMB_LIT"]} if system == "256" else {}),
        },
    )
    return TPOT_THEME


def color_system() -> str:
    """truecolor or 256, exactly as Textual renders: its COLOR_SYSTEM (TEXTUAL_COLOR_SYSTEM when it was
    imported, which the launcher sets from TPOT_COLORS / prefs.py), for auto Rich's look at COLORTERM
    and TERM. Read from the prefs here, palette and output could disagree."""
    from textual import constants
    system = constants.COLOR_SYSTEM or "auto"
    if system == "auto":
        from rich.console import Console
        system = Console(force_terminal=True).color_system or "truecolor"
    return "truecolor" if system == "truecolor" else "256"


STYLE: dict = {}
TPOT_THEME: Theme = build(color_system())


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
