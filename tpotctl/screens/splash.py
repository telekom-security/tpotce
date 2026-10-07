"""The animated splash when the menu starts (tpotctl/splash_anim.py draws the frames).

About eight seconds, any key or click goes on at once; below 80 x 24 there is none.
"""

import time

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import Screen
from textual.widgets import Static

from tpotctl import splash_anim
from tpotctl.splash_anim import Splash, variant_for

FPS = 20


def fits(width: int, height: int) -> bool:
    return variant_for(width, height) is not None


class SplashScreen(Screen):

    def __init__(self, version: str = ""):
        super().__init__(id="splash")
        self.version = version
        self.splash = None

    def choose(self) -> bool:
        """The variant for the terminal now; False when it is too small for any."""
        variant = variant_for(self.app.size.width, self.app.size.height)
        if variant is None:
            return False
        if self.splash is None or self.splash.variant != variant:
            self.splash = Splash(self.version, variant)
        return True

    def on_resize(self) -> None:
        if not self.choose():
            self.leave()

    def compose(self) -> ComposeResult:
        if not self.choose():
            self.splash = Splash(self.version, "80x24")      # the app only shows it where it fits
        with Middle():
            with Center():
                yield Static(self.splash.frame(0.0), id="splash-canvas")

    def on_mount(self) -> None:
        self.started = time.monotonic()
        self.ticker = self.set_interval(1 / FPS, self.tick)

    def tick(self) -> None:
        # the frame of the time now: a slow tick skips frames instead of slowing the animation down
        elapsed = time.monotonic() - self.started
        if elapsed >= splash_anim.DURATION:
            self.leave()
            return
        self.query_one("#splash-canvas", Static).update(self.splash.frame(elapsed))

    def leave(self) -> None:
        ticker = getattr(self, "ticker", None)
        if ticker is not None:
            ticker.stop()
        if self.is_current:
            self.app.pop_screen()

    def on_key(self, event) -> None:
        event.stop()
        self.leave()

    def on_click(self) -> None:
        self.leave()
