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
# (SSH without COLORTERM from most terminals, tmux): left to the nearest entry, the dark magentas turn into maroon.
PALETTE_256 = {"INK": "#000000", "COMB": "#121212", "COMB_LIT": "#5f005f", "WAX": "#87005f",
               "MAGENTA": "#d70087", "PETROL": "#005f5f", "GLASS": "#ffffff", "MIST": "#87afd7",
               "KEY": "#5fafd7", "ASH": "#a8a8a8", "OK": "#5faf5f", "WARN": "#ffaf00", "ERROR": "#d75f5f"}
# and as the 16 ANSI colours (TERM=xterm as PuTTY sets it, screen): panels dark grey (8), inactive
# borders magenta (5), focus bright magenta (13), so the three stay apart and light text stays readable
# on the panels; the values are the VGA entries Rich downgrades to (rich._palettes.STANDARD_PALETTE),
# the terminal's own palette gives the shades
PALETTE_16 = {"INK": "#000000", "COMB": "#000000", "COMB_LIT": "#555555", "WAX": "#aa00aa",
              "MAGENTA": "#ff55ff", "PETROL": "#00aaaa", "GLASS": "#ffffff", "MIST": "#aaaaaa",
              "KEY": "#55ffff", "ASH": "#aaaaaa", "OK": "#55ff55", "WARN": "#ffff55", "ERROR": "#ff5555"}
PALETTES = {"truecolor": TRUECOLOR, "256": PALETTE_256, "16": PALETTE_16}


def build(system: str) -> Theme:
    """STYLE and TPOT_THEME from the true colours, PALETTE_256 ("256") or PALETTE_16 ("16")."""
    global STYLE, TPOT_THEME
    p = PALETTES.get(system, TRUECOLOR)
    # Textual mixes some colours itself (alpha, muted, darkened); with fewer colours they land on maroon,
    # so they come from the palette there
    few = {} if system not in ("256", "16") else {
        "block-cursor-blurred-background": p["COMB_LIT"],
        "primary-muted": p["COMB_LIT"], "accent-muted": p["COMB_LIT"],
        "primary-background-darken-1": p["COMB"],
    }
    if system == "16":
        # solid entries where blends vanish: the cursor on magenta (5), the selection on the focused field
        # (WAX) in grey, no zebra (half of grey is black)
        few.update({"block-cursor-background": p["WAX"], "input-selection-background": p["COMB_LIT"],
                    "stripe": p["INK"]})
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
            # every other row of a table
            "stripe": f"{p['COMB_LIT']} 50%",
            # the background of a focused field; magenta at 35 % turns into maroon with fewer colours
            "focus-tint": f"{p['MAGENTA']} 35%" if not few else p["WAX"],
            # what a dialog leaves of the page below it: dimmed further, magenta goes red with 256
            "modal-dim": {"256": f"{p['INK']} 40%", "16": f"{p['INK']} 0%"}.get(system, f"{p['INK']} 70%"),
            **few,
        },
    )
    return TPOT_THEME


def color_system() -> str:
    """truecolor, 256 or 16, exactly as Textual renders: its COLOR_SYSTEM (TEXTUAL_COLOR_SYSTEM when it was
    imported, which the launcher and the customizer set by prefs.apply_color_system: TPOT_COLORS,
    tpot.json or the rule of the scripts, prefs.detect_colors), for auto (nothing set it, or a console
    of Windows) Rich's look at the terminal. Read from the prefs here, palette and output could disagree."""
    from textual import constants
    system = constants.COLOR_SYSTEM or "auto"
    if system == "auto":
        from rich.console import Console
        # as Textual builds its console: a legacy Windows console does not count
        system = Console(force_terminal=True, legacy_windows=False).color_system or "truecolor"
    return {"truecolor": "truecolor", "256": "256"}.get(system, "16")       # standard (and windows): 16


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
