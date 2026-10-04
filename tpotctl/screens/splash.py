"""The animated splash when the menu starts (tpotctl/splash_anim.py draws the frames).

About two seconds, any key or click goes on at once.
"""

import time

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import Screen
from textual.widgets import Static

from tpotctl.splash_anim import DURATION, Splash, best

FPS = 30


def fits(width: int, height: int) -> bool:
    return best("", width, height) is not None


class SplashScreen(Screen):

    def __init__(self, version: str = ""):
        super().__init__(id="splash")
        self.version = version

    def on_resize(self) -> None:
        self.choose()

    def choose(self) -> None:
        self.splash = best(self.version, self.app.size.width, self.app.size.height) or Splash(self.version)

    def compose(self) -> ComposeResult:
        self.choose()
        with Middle():
            with Center():
                yield Static(self.splash.frame(0.0), id="splash-canvas")

    def on_mount(self) -> None:
        self.started = time.monotonic()
        self.ticker = self.set_interval(1 / FPS, self.tick)

    def tick(self) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed >= DURATION:
            self.leave()
            return
        self.query_one("#splash-canvas", Static).update(self.splash.frame(elapsed))

    def leave(self) -> None:
        self.ticker.stop()
        if self.is_current:
            self.app.pop_screen()

    def on_key(self, event) -> None:
        event.stop()
        self.leave()

    def on_click(self) -> None:
        self.leave()
