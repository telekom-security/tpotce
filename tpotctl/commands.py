"""ctrl+p in the T-Pot Manager: jump to a page or a setting, run an action, change the icons or the colours."""

from functools import partial
from typing import Callable, Iterator, Tuple

from textual.command import DiscoveryHit, Hit, Hits, Provider

from tpotctl import prefs

Command = Tuple[str, str, Callable]


class TpotCommands(Provider):

    def commands(self) -> Iterator[Command]:
        app = self.app
        for key, title, _cls, _host in app.panes:
            yield f"Go to {title}", "page", partial(app.goto, key)
        host = app.backend.linux_host()
        for name, help_text, callback in (
                ("Start T-Pot", "sudo systemctl start tpot.service", partial(app.service, "start")),
                ("Stop T-Pot", "sudo systemctl stop tpot.service", partial(app.service, "stop")),
                ("Restart T-Pot", "sudo systemctl restart tpot.service", partial(app.service, "restart")),
                ("Update T-Pot", "update.sh, writes a backup first", partial(app.run_update, ["-y"])),
                ("Restore a backup", "restore.sh, the parts you choose", app.run_restore)):
            # shown everywhere, so you find them; off a T-Pot host they say why not
            yield (name, help_text, callback) if host else \
                (name, "needs a T-Pot host", partial(app.not_here, name))
        yield "Check the settings", "as T-Pot does on start, tpot env check", app.check_settings
        if host and app.backend.installable():
            yield "Install T-Pot", "the installer assistant, tpot install", app.start_install
        yield "Open the customizer", "edition and services", app.action_customize
        yield "Rebuild the T-Pot Manager's packages", "tpot setup --force", partial(app.goto, "update")
        for key, value in app.setting_fixes().items():
            yield f"Fix {key}", f"this host needs {value}, the Settings page has it ready", \
                partial(app.fix_setting, key)
        # every host: macOS and Windows switch to MAC_WIN
        in_use = app.backend.edition_current()[0].lower()
        for choice in app.backend.editions():
            if choice.key != in_use:
                yield (f"Switch to the {choice.title} edition", f"tpot edition set {choice.key}",
                       partial(app.switch_edition, choice.key))
        yield "Find Ollama", "for Beelzebub and Galah, LLM page", partial(app.llm_action, "find", "galah")
        for service, title in (("beelzebub", "Beelzebub"), ("galah", "Galah")):
            yield f"Test the LLM of {title}", "a short prompt to its model", partial(app.llm_action, "test", service)
        for mode in ("unicode", "nerd", "ascii"):
            yield f"Icons: {mode}", {"unicode": "symbols every font has", "nerd": "needs a Nerd Font",
                                     "ascii": "plain characters"}[mode], partial(app.set_icons, mode)
        for mode in prefs.COLORS:
            yield f"Colours: {mode}", {"auto": "what the terminal says", "truecolor": "24 bit, i.e. over SSH",
                                       "256": "the palette for 256 colours"}[mode], partial(app.set_colors, mode)
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
