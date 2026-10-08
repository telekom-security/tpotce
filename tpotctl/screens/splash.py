"""The animated splash when the menu starts (tpotctl/splash_anim.py draws the frames).

About eight seconds, any key or click goes on at once; below 80 x 24 there is none.
"""

import time

from textual.app import ComposeResult
from textual.containers import Center, Middle, Vertical
from textual.screen import Screen
from textual.widgets import Static

from tpotctl import splash_anim
from tpotctl.splash_anim import Splash, variant_for

# like the template; every frame only sends the rows that changed (over SSH every byte counts)
FPS = 15


def fits(width: int, height: int) -> bool:
    return variant_for(width, height) is not None


class SplashScreen(Screen):

    def __init__(self, version: str = ""):
        super().__init__(id="splash")
        self.version = version
        self.splash = None
        self.done = False           # its end has come; a dialog over it may still keep it on the stack

    def choose(self) -> bool:
        """The variant for the terminal now; False when it is too small for any."""
        variant = variant_for(self.app.size.width, self.app.size.height)
        if variant is None:
            return False
        if self.splash is None or self.splash.variant != variant:
            self.splash = Splash(self.version, variant)
            self.shown = None
        return True

    def rows(self):
        return [Static(self.splash.line(row), classes="splash-row") for row in self.splash.cells(0.0)]

    def on_resize(self) -> None:
        before = self.splash
        if not self.choose():
            self.leave()
        elif self.splash is not before and self.query("#splash-canvas"):
            canvas = self.query_one("#splash-canvas", Vertical)
            canvas.remove_children()
            canvas.mount(*self.rows())

    def compose(self) -> ComposeResult:
        if not self.choose():
            self.splash = Splash(self.version, "80x24")      # the app only shows it where it fits
        self.shown = None
        with Middle():
            with Center():
                with Vertical(id="splash-canvas"):
                    yield from self.rows()

    def on_mount(self) -> None:
        self.started = time.monotonic()
        self.ticker = self.set_interval(1 / FPS, self.tick)

    def tick(self) -> None:
        # the frame of the time now: a slow tick skips frames instead of slowing the animation down
        elapsed = time.monotonic() - self.started
        if elapsed >= splash_anim.DURATION:
            self.leave()
            return
        self.show(elapsed)

    def show(self, elapsed: float) -> int:
        """The frame at elapsed seconds; only the rows whose cells changed are updated (cells() gives
        back the same list for a row that stays). Gives back how many."""
        cells = self.splash.cells(elapsed)
        widgets = list(self.query(".splash-row").results(Static))
        if len(widgets) != len(cells):
            return 0                                          # a new variant is being mounted
        shown = self.shown or [None] * len(cells)
        updated = 0
        for widget, row, old in zip(widgets, cells, shown):
            if row is not old and row != old:
                widget.update(self.splash.line(row))
                updated += 1
        self.shown = cells
        return updated

    def leave(self) -> None:
        """The end (its time, a key, a click, a terminal too small): the menu, then the notices that
        waited for it. Under a dialog (is_current is true there as well, it shows through) it pops
        nothing, the dialog stays; it goes when the dialog closes (on_screen_resume)."""
        self.done = True
        ticker = getattr(self, "ticker", None)
        if ticker is not None:
            ticker.stop()
        if self.app.screen is self:
            self.app.pop_screen()
        release = getattr(self.app, "release_notices", None)
        if release is not None:
            release()

    def on_screen_resume(self) -> None:
        """In front again (the dialog over it closed): a splash whose end came meanwhile goes now."""
        if self.done and self.app.screen is self:
            self.app.pop_screen()

    def on_key(self, event) -> None:
        event.stop()
        self.leave()

    def on_click(self) -> None:
        self.leave()
