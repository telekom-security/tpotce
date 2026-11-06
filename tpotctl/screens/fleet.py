"""Dialogs and progress screen for HIVE-managed sensor fleet updates."""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Union

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Static

from tpotctl import fleet as tfleet, ops, theme
from tpotctl.screens.dialogs import ConfirmDialog
from tpotctl.widgets.nav import DialogTitle, NavDataTable, NavInput, NavModal, NavScroll


@dataclass
class FleetRequest:
    names: List[str]
    all_sensors: bool
    parallel: Union[int, str]
    password: str = field(default="", repr=False)
    passwordless: bool = False


@dataclass
class FleetCredentials:
    password: str = field(default="", repr=False)
    passwordless: bool = False


class FleetUpdateDialog(NavModal):
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog compact"):
            yield DialogTitle("Update sensors")
            with NavScroll():
                yield Static("By default, update only sensors verified behind this HIVE. "
                             "The canary runs first; parallel updates start only after it succeeds.",
                             classes="hint")
                yield NavInput(placeholder="sensor names separated by commas (blank: behind)", id="fleet-names")
                yield Checkbox("Update all accessible sensors", id="fleet-all")
                yield NavInput("1", placeholder="1-1000 or all", id="fleet-parallel")
                yield Static("", id="fleet-error", classes="error-text")
            with Horizontal(classes="actions"):
                yield Button("Back", id="fleet-back")
                yield Button("Prepare", id="fleet-start", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#fleet-names", Input).focus()
        self.refresh_fields()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self.refresh_fields()

    def refresh_fields(self) -> None:
        names = [name.strip() for name in self.query_one("#fleet-names", Input).value.split(",") if name.strip()]
        all_sensors = self.query_one("#fleet-all", Checkbox).value
        parallel = self.query_one("#fleet-parallel", Input).value.strip()
        problem = ""
        if names and all_sensors:
            problem = "Choose names or all sensors, not both."
        elif len(names) != len(set(names)):
            problem = "A sensor name may appear only once."
        elif any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", name) for name in names):
            problem = "Sensor names may contain only letters, numbers, dots, underscores and hyphens."
        elif parallel != "all" and (not parallel.isascii() or not parallel.isdigit()
                                    or not 1 <= int(parallel or "0") <= 1000):
            problem = "Parallelism must be 1-1000 or all."
        self.query_one("#fleet-error", Static).update(Text(problem, style=theme.color("error")))
        self.query_one("#fleet-start", Button).disabled = bool(problem)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "fleet-start":
            self.dismiss(None)
            return
        names = [name.strip() for name in self.query_one("#fleet-names", Input).value.split(",") if name.strip()]
        parallel_text = self.query_one("#fleet-parallel", Input).value.strip()
        parallel = parallel_text if parallel_text == "all" else int(parallel_text)
        self.dismiss(FleetRequest(names, self.query_one("#fleet-all", Checkbox).value, parallel))

    def action_cancel(self) -> None:
        self.dismiss(None)


class FleetPasswordDialog(NavModal):
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog compact"):
            yield DialogTitle("Sensor sudo")
            with NavScroll():
                yield Checkbox("sudo on the sensors needs no password", id="fleet-sudo-nopass")
                yield NavInput(placeholder="sudo password for the sensors", id="fleet-sudo-password", password=True)
                yield Static("", id="fleet-sudo-error", classes="error-text")
            with Horizontal(classes="actions"):
                yield Button("Back", id="fleet-sudo-back")
                yield Button("Continue", id="fleet-sudo-continue", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#fleet-sudo-password", Input).focus()
        self.refresh_fields()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        if self.query_one("#fleet-sudo-nopass", Checkbox).value:
            self.query_one("#fleet-sudo-password", Input).value = ""
        self.refresh_fields()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self.refresh_fields()

    def refresh_fields(self) -> None:
        no_password = self.query_one("#fleet-sudo-nopass", Checkbox).value
        password = self.query_one("#fleet-sudo-password", Input)
        password.display = not no_password
        problem = "" if no_password or (password.value and not any(c in password.value for c in "\r\n\0")) \
            else "Enter the sensor sudo password, or select passwordless sudo."
        self.query_one("#fleet-sudo-error", Static).update(Text(problem, style=theme.color("error")))
        self.query_one("#fleet-sudo-continue", Button).disabled = bool(problem)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "fleet-sudo-continue":
            password = self.query_one("#fleet-sudo-password", Input)
            password.value = ""
            self.dismiss(None)
            return
        self.refresh_fields()
        if self.query_one("#fleet-sudo-continue", Button).disabled:
            return
        password = self.query_one("#fleet-sudo-password", Input)
        credentials = FleetCredentials(
            "" if self.query_one("#fleet-sudo-nopass", Checkbox).value else password.value,
            self.query_one("#fleet-sudo-nopass", Checkbox).value,
        )
        password.value = ""
        self.dismiss(credentials)

    def action_cancel(self) -> None:
        self.query_one("#fleet-sudo-password", Input).value = ""
        self.dismiss(None)


class FleetUpdateScreen(Screen):
    """Runs or resumes a fleet update; no password is written to persistent state."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, request: Optional[FleetRequest], registry, *, run_id: str = "", coordinator_factory=None):
        super().__init__(id="fleet-update")
        self.request, self.registry, self.run_id = request, registry, run_id
        self.coordinator_factory = coordinator_factory
        self.coordinator = None
        self.password = request.password if request is not None else ""
        self.passwordless = request.passwordless if request is not None else False
        self.state = None
        self.busy = False
        self.prompted_attempts = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="fleet-body"):
            yield Static(Text("Sensor fleet update", style=f"bold {theme.color('magenta')}"), id="fleet-title")
            yield Static("Preparing the selected sensors ...", id="fleet-summary")
            yield NavDataTable(id="fleet-table", cursor_type="row", zebra_stripes=True, fixed_columns=1)
            yield Static("", id="fleet-detail")
            with Vertical(id="fleet-actions"):
                with Horizontal(classes="actions"):
                    yield Button("Refresh status", id="fleet-refresh")
                    yield Button("Retry", id="fleet-retry", variant="primary")
                    yield Button("Skip", id="fleet-skip")
                    yield Button("Roll back", id="fleet-rollback")
                with Horizontal(classes="actions"):
                    yield Button("Abort", id="fleet-abort")
                    yield Button("Extend deadline", id="fleet-extend")
                    yield Button("Discard run", id="fleet-discard")
                    yield Button("Back", id="fleet-back")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(NavDataTable)
        table.add_columns("Sensor", "Update", "Previous version", "Details")
        self.update_actions()
        self.check_saved()
        self.set_interval(10.0, self.poll)

    def on_unmount(self) -> None:
        self.password = ""
        if self.request is not None:
            self.request.password = ""

    @work(thread=True, exclusive=True, group="fleet-state")
    def check_saved(self) -> None:
        try:
            self.coordinator = self.make_coordinator()
            runs = self.coordinator.store.list_runs()
            problem = ""
        except tfleet.FleetError as err:
            runs, problem = [], str(err)
        if self.is_attached:
            self.app.call_from_thread(self.saved_checked, runs, problem)

    def make_coordinator(self):
        if self.coordinator_factory is not None:
            return self.coordinator_factory(registry=self.registry)
        return tfleet.FleetCoordinator(registry=self.registry)

    def saved_checked(self, runs, problem: str) -> None:
        if problem:
            self.query_one("#fleet-summary", Static).update(Text(problem, style=theme.color("error")))
            return
        pending = [run for run in runs if run["phase"] in (
            "preparing", "running", "paused", "aborting", "prepared", "precheck_failed")]
        if self.run_id:
            self.begin_resume(self.run_id)
        elif pending:
            self.choose_saved(pending)
        elif self.request is None:
            self.request_update()
        else:
            self.prepare()

    def request_update(self) -> None:
        def requested(request) -> None:
            if request is None:
                self.dismiss(None)
                return
            self.request = request
            self.password = request.password
            self.passwordless = request.passwordless
            self.prepare()

        self.app.push_screen(FleetUpdateDialog(), requested)

    def choose_saved(self, runs) -> None:
        from tpotctl.screens.dialogs import ChoiceDialog
        active = any(target["phase"] in ("running", "checking", "rollback_running")
                     for run in runs for target in run["targets"])
        choices = [f"{'Inspect' if run['phase'] == 'preparing' else 'Resume'} "
                   f"{run['run_id']} ({run['phase']})" for run in runs]
        if not active:
            choices.extend(f"Discard {run['run_id']}" for run in runs)
            choices.append("Start a new fleet run")

        def selected(index):
            if index is None:
                self.dismiss(None)
                return
            if index < len(runs):
                self.begin_resume(runs[index]["run_id"])
                return
            if index < len(runs) * 2:
                run = runs[index - len(runs)]
                self.confirm_discard(run["run_id"], start_new=False)
                return
            self.confirm_discard_many([run["run_id"] for run in runs])

        self.app.push_screen(ChoiceDialog("Saved fleet run", choices), selected)

    def confirm_discard(self, run_id: str, *, start_new: bool) -> None:
        self.app.push_screen(
            ConfirmDialog(f"Discard fleet run {run_id}?", "This removes the saved and remote precheck state.",
                          yes="Discard"),
            lambda yes: self.start_discard(run_id, start_new) if yes else self.dismiss(None),
        )

    def start_discard(self, run_id: str, start_new: bool) -> None:
        self.busy = True
        self.update_actions()
        self.discard_run(run_id, start_new)

    def confirm_discard_many(self, run_ids) -> None:
        self.app.push_screen(
            ConfirmDialog("Start a new fleet run?", "The stopped saved runs will be removed first.", yes="Continue"),
            lambda yes: self.discard_many(run_ids) if yes else self.dismiss(None),
        )

    @work(thread=True, exclusive=True, group="fleet-discard")
    def discard_run(self, run_id: str, start_new: bool) -> None:
        try:
            self.coordinator.discard(run_id)
            problem = ""
        except tfleet.FleetError as err:
            problem = str(err)
        if self.is_attached:
            self.app.call_from_thread(self.discarded, problem, start_new)

    def discard_many(self, run_ids) -> None:
        self.busy = True
        self.update_actions()
        self.discard_saved_runs(run_ids)

    @work(thread=True, exclusive=True, group="fleet-discard")
    def discard_saved_runs(self, run_ids) -> None:
        try:
            for run_id in run_ids:
                self.coordinator.discard(run_id)
            problem = ""
        except tfleet.FleetError as err:
            problem = str(err)
        if self.is_attached:
            self.app.call_from_thread(self.discarded, problem, True)

    def discarded(self, problem: str, start_new: bool) -> None:
        self.busy = False
        if problem:
            self.query_one("#fleet-summary", Static).update(Text(problem, style=theme.color("error")))
            self.update_actions()
        elif start_new:
            if self.request is None:
                self.request_update()
            else:
                self.prepare()
        else:
            self.password = ""
            if self.request is not None:
                self.request.password = ""
            self.dismiss(None)

    def prepare(self) -> None:
        self.busy = True
        self.query_one("#fleet-summary", Static).update("Preparing every selected sensor ...")
        self.update_actions()
        self.with_credentials(self.prepare_worker)

    def with_credentials(self, action) -> None:
        if self.password or self.passwordless:
            action()
            return
        self.app.push_screen(
            FleetPasswordDialog(),
            lambda credentials: self.credentials_collected(credentials, action),
        )

    def credentials_collected(self, credentials, action) -> None:
        if credentials is None:
            self.busy = False
            self.update_actions()
            return
        self.password = credentials.password
        self.passwordless = credentials.passwordless
        if self.request is not None:
            self.request.password = self.password
            self.request.passwordless = self.passwordless
        action()

    @work(thread=True, exclusive=True, group="fleet-prepare")
    def prepare_worker(self) -> None:
        from tpotctl import sensors as tsensors
        try:
            if self.request is None:
                raise tfleet.FleetError("Choose the sensors to update first.")
            ops.require_linux_host("tpot sensors update")
            ops.require_config_current()
            repo = os.environ.get("TPOT_REPO_URL") or self.registry.env.get("TPOT_REPO_URL") \
                or ops.git(self.registry.repo_dir, "remote", "get-url", "origin")
            branch = os.environ.get("TPOT_BRANCH") or self.registry.env.get("TPOT_BRANCH") \
                or ops.git(self.registry.repo_dir, "branch", "--show-current")
            version = self.registry.env.get("TPOT_VERSION") or ""
            if not repo or not branch or not version:
                raise ValueError("The HIVE repository, current branch and TPOT_VERSION are required.")
            state = self.coordinator.prepare(
                self.registry, self.app.backend.sensor_status(), repo=repo, branch=branch, version=version,
                names=self.request.names, all_sensors=self.request.all_sensors,
                parallel=self.request.parallel, password=self.password,
            )
            problem = ""
        except (ops.OpsError, tsensors.SensorsError, tfleet.FleetError, OSError, ValueError) as err:
            state, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.prepared, state, problem)

    def prepared(self, state, problem: str) -> None:
        self.busy = False
        if problem:
            self.query_one("#fleet-summary", Static).update(Text(problem, style=theme.color("error")))
            self.update_actions()
            return
        self.state, self.run_id = state, state["run_id"]
        self.render_state()
        if state["phase"] == "prepared":
            self.prompt_start()

    def prompt_start(self) -> None:
        if self.state is None:
            return
        body = "\n".join(
            [f"{target['name']}: {target['previous_version']} -> {self.state['version']}"
             for target in self.state["targets"]]
            + [line for target in self.state["targets"] for line in target["losses"]]
        )
        self.app.push_screen(
            ConfirmDialog("Start the sensor fleet update?", body or "All prechecks passed.", yes="Start"),
            lambda yes: self.approve_start(yes),
        )

    def approve_start(self, yes: bool) -> None:
        if not yes or self.state is None:
            return
        accepted = {target["name"]: target["losses"] for target in self.state["targets"]}
        self.busy = True
        self.update_actions()
        self.with_credentials(lambda: self.start_worker(accepted))

    @work(thread=True, exclusive=True, group="fleet-start")
    def start_worker(self, accepted) -> None:
        from tpotctl import sensors as tsensors
        try:
            self.coordinator.start(self.run_id, accepted_losses=accepted, password=self.password)
            state, problem = self.coordinator.store.read(self.run_id), ""
        except (tfleet.FleetError, tsensors.SensorsError) as err:
            state, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.operation_finished, state, problem)

    def begin_resume(self, run_id: str) -> None:
        self.run_id = run_id
        self.refresh_worker()

    @work(thread=True, exclusive=True, group="fleet-refresh")
    def refresh_worker(self) -> None:
        from tpotctl import sensors as tsensors
        try:
            state = self.coordinator.store.read(self.run_id)
            retry_ready = state["phase"] == "paused" and any(
                target["phase"] == "prepared" and target.get("attempt", 1) > 1 for target in state["targets"])
            if state["phase"] in ("running", "paused", "aborting") and not retry_ready:
                self.coordinator.resume(self.run_id, self.app.backend.sensor_status(), password=self.password)
                state = self.coordinator.store.read(self.run_id)
            problem = ""
        except (tfleet.FleetError, tsensors.SensorsError) as err:
            state, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.operation_finished, state, problem)

    def operation_finished(self, state, problem: str) -> None:
        self.busy = False
        if problem:
            self.query_one("#fleet-summary", Static).update(Text(problem, style=theme.color("error")))
            self.update_actions()
        elif state is not None:
            self.state = state
            self.render_state()
            if state["phase"] == "prepared":
                self.prompt_start()
            else:
                retry = self.selected_target()
                if retry is not None and retry["phase"] == "prepared" and retry.get("attempt", 1) > 1:
                    attempt_key = (retry["name"], retry["attempt"])
                    if attempt_key not in self.prompted_attempts:
                        self.prompted_attempts.add(attempt_key)
                        self.prompt_retry(retry)

    def poll(self) -> None:
        if self.busy or self.state is None or self.state["phase"] not in ("running", "paused", "aborting"):
            return
        self.busy = True
        self.update_actions()
        self.refresh_worker()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "fleet-back":
            self.action_back()
        elif button == "fleet-refresh":
            self.poll()
        elif button == "fleet-discard" and self.run_id:
            self.app.push_screen(
                ConfirmDialog(f"Discard fleet run {self.run_id}?", "Running workers cannot be discarded.",
                              yes="Discard"),
                lambda yes: self.start_discard(self.run_id, False) if yes else None,
            )
        elif button == "fleet-retry":
            target = self.selected_target()
            if target is not None and target["phase"] == "prepared" and target.get("attempt", 1) > 1:
                self.prompt_retry(target)
            else:
                self.resolve_with_credentials("retry")
        elif button in ("fleet-skip", "fleet-rollback", "fleet-abort"):
            choice = button.removeprefix("fleet-")
            if choice == "abort" or (choice == "skip" and not self.has_pending_target()):
                self.resolve_with_busy(choice)
            else:
                self.resolve_with_credentials(choice)
        elif button == "fleet-extend":
            target = self.selected_target()
            if target:
                self.busy = True
                self.update_actions()
                self.extend_worker(target["name"])

    def selected_target(self):
        if self.state is None or not self.state["targets"]:
            return None
        table = self.query_one(NavDataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            return self.state["targets"][0]
        name = str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)
        return next((target for target in self.state["targets"] if target["name"] == name), None)

    def has_pending_target(self) -> bool:
        target = self.selected_target()
        return bool(self.state and any(
            item is not target and item["phase"] == "prepared" for item in self.state["targets"]))

    def resolve_with_busy(self, choice: str) -> None:
        self.busy = True
        self.update_actions()
        self.resolve(choice)

    def resolve_with_credentials(self, choice: str) -> None:
        self.busy = True
        self.update_actions()
        self.with_credentials(lambda: self.resolve(choice))

    @work(thread=True, exclusive=True, group="fleet-decision")
    def resolve(self, choice: str) -> None:
        target = self.selected_target()
        if target is None:
            return
        try:
            state = self.coordinator.resolve_failure(self.run_id, target["name"], choice, password=self.password)
            problem = ""
        except tfleet.FleetError as err:
            state, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.resolved, state, problem, choice)

    def resolved(self, state, problem: str, choice: str) -> None:
        self.busy = False
        if problem:
            self.query_one("#fleet-summary", Static).update(Text(problem, style=theme.color("error")))
            self.update_actions()
            return
        self.state = state
        target = self.selected_target()
        if choice == "retry" and target is not None and target["phase"] == "prepared":
            self.prompt_retry(target)
        else:
            self.render_state()

    def prompt_retry(self, target) -> None:
        body = "\n".join(target["losses"]) or "The retry precheck passed."
        self.app.push_screen(
            ConfirmDialog(f"Start retry for {target['name']}?", body, yes="Start"),
            lambda yes: self.retry_start(target, yes),
        )

    def retry_start(self, target, yes: bool) -> None:
        if yes:
            self.busy = True
            self.update_actions()
            self.with_credentials(lambda: self.retry_worker(target["name"], list(target["losses"])))
        else:
            self.render_state()

    @work(thread=True, exclusive=True, group="fleet-start")
    def retry_worker(self, name: str, losses: list) -> None:
        try:
            state = self.coordinator.start_retry(self.run_id, name, accepted_losses=losses,
                                                 password=self.password)
            problem = ""
        except tfleet.FleetError as err:
            state, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.operation_finished, state, problem)

    @work(thread=True, exclusive=True, group="fleet-extend")
    def extend_worker(self, name: str) -> None:
        try:
            state = self.coordinator.extend_deadline(self.run_id, name)
            problem = ""
        except tfleet.FleetError as err:
            state, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.operation_finished, state, problem)

    def render_state(self) -> None:
        if self.state is None:
            return
        targets = self.state["targets"]
        self.query_one("#fleet-table", NavDataTable).sync_rows([
            (target["name"], (
                target["name"], target["phase"], target["previous_version"],
                target.get("last_error") or "; ".join(target.get("warnings", [])),
            ))
            for target in targets
        ])
        self.query_one("#fleet-summary", Static).update(
            f"Run {self.run_id} — {self.state['phase']} — {sum(t['phase'] == 'completed' for t in targets)} "
            f"completed of {len(targets)} sensors")
        detail = "\n".join(line for target in targets for line in target.get("losses", []))
        self.query_one("#fleet-detail", Static).update(Text(detail, style=theme.color("warn")))
        self.update_actions()

    def update_actions(self) -> None:
        state = self.state
        active = state is not None and any(target["phase"] in (
            "running", "checking", "rollback_running") for target in state["targets"])
        target = self.selected_target() if state is not None else None
        failed = target is not None and target["phase"] in ("failed", "rollback_failed")
        self.query_one("#fleet-refresh", Button).disabled = self.busy or not active
        self.query_one("#fleet-retry", Button).display = bool(failed)
        self.query_one("#fleet-skip", Button).display = bool(failed)
        self.query_one("#fleet-rollback", Button).display = bool(failed and target.get("archive"))
        self.query_one("#fleet-abort", Button).display = bool(failed or active)
        self.query_one("#fleet-extend", Button).display = bool(
            target is not None and target["phase"] == "checking")
        if target is not None and target["phase"] == "prepared" and target.get("attempt", 1) > 1:
            self.query_one("#fleet-retry", Button).display = True
            self.query_one("#fleet-retry", Button).label = "Start retry"
        else:
            self.query_one("#fleet-retry", Button).label = "Retry"
        self.query_one("#fleet-discard", Button).display = bool(state is not None and not active)
        for button_id in ("fleet-retry", "fleet-skip", "fleet-rollback", "fleet-abort",
                          "fleet-extend", "fleet-discard", "fleet-back"):
            button = self.query_one(f"#{button_id}", Button)
            button.disabled = self.busy or not button.display

    def on_data_table_row_highlighted(self, _event) -> None:
        self.update_actions()

    def action_back(self) -> None:
        if self.busy:
            return
        self.password = ""
        self.request.password = ""
        self.dismiss(None)
