"""ctrl+p in tpot: jump to a page or a setting, run an action, change the icons."""

from functools import partial
from typing import Callable, Iterator, Tuple

from textual.command import DiscoveryHit, Hit, Hits, Provider

Command = Tuple[str, str, Callable]


class TpotCommands(Provider):

    def commands(self) -> Iterator[Command]:
        app = self.app
        for key, title, _cls, _host in app.panes:
            yield f"Go to {title}", "page", partial(app.goto, key)
        if app.backend.linux_host():
            yield "Start T-Pot", "sudo systemctl start tpot", partial(app.service, "start")
            yield "Stop T-Pot", "sudo systemctl stop tpot", partial(app.service, "stop")
            yield "Restart T-Pot", "sudo systemctl restart tpot", partial(app.service, "restart")
            yield "Update T-Pot", "update.sh, writes a backup first", partial(app.script, "update.sh", ["-y"])
        yield "Open the customizer", "edition and services", app.action_customize
        for mode in ("unicode", "nerd", "ascii"):
            yield f"Icons: {mode}", {"unicode": "symbols every font has", "nerd": "needs a Nerd Font",
                                     "ascii": "plain characters"}[mode], partial(app.set_icons, mode)
        for key, title in app.setting_keys():
            yield f"{title} ({key})", "setting", partial(app.goto_setting, key)

    async def discover(self) -> Hits:
        for name, help_text, callback in self.commands():
            if help_text != "setting":
                yield DiscoveryHit(name, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text, callback in self.commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)
