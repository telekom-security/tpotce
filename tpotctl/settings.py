"""The settings in ~/tpotce/.env: list, check and change them against the schema.

Used by `tpot env` and the Settings page of the menu. A change is only written if
the changed keys are valid afterwards; problems of keys nobody touched do not block
it, tpotinit reports those on the next start anyway.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from tpotctl import envschema, ops
from tpotctl.bootstrap import REPO_DIR
from tpotctl.envfile import EnvError, EnvFile

# who maintains the keys tpot only shows
MANAGED_BY = {
    "WEB_USER": "tpot users",
    "LS_WEB_USER": "tpot sensors",
    "TPOT_HIVE_USER": "tpot sensors on the HIVE",
}


class SettingsError(Exception):
    """A change that is not written."""


@dataclass
class Settings:
    path: str
    env: EnvFile
    schema: Dict[str, envschema.Rule]
    services: Set[str]

    @property
    def values(self) -> Dict[str, str]:
        return self.env.values()

    def relevant(self, include_all: bool = False, values: Optional[Dict[str, str]] = None) -> List[envschema.Rule]:
        """The keys that matter for this type of T-Pot and these services, in schema order."""
        values = self.values if values is None else values
        return [rule for rule in self.schema.values()
                if include_all or envschema.applies(rule, values, self.services)]

    def problems(self, values: Optional[Dict[str, str]] = None) -> List[envschema.Problem]:
        return envschema.validate(self.values if values is None else values, self.services, self.schema)

    def blocking(self, problems: List[envschema.Problem], changes: Dict[str, str]) -> List[envschema.Problem]:
        """What stops a change: errors of changed keys, and a value tpotinit would replace by its
        default (on_invalid: default only warns there, but tpot does not write such a value)."""
        out = []
        for problem in problems:
            if problem.key not in changes:
                continue
            rule = self.schema.get(problem.key)
            if problem.level == "error" or (rule is not None and rule.on_invalid and changes[problem.key]):
                out.append(problem)
        return out

    def why_fixed(self, key: str) -> str:
        if key in MANAGED_BY:
            return f"managed with {MANAGED_BY[key]}"
        return "fixed by the release or the installer"

    def change(self, changes: Dict[str, str]) -> List[envschema.Problem]:
        """Write the changes, give back the warnings that remain; SettingsError if not written."""
        for key, value in changes.items():
            rule = self.schema.get(key)
            if rule is None:
                raise SettingsError(f"{key} is not a T-Pot setting, see tpot env list --all")
            if not rule.editable:
                raise SettingsError(f"{key} is {self.why_fixed(key)}, tpot does not change it")
        values = dict(self.values)
        values.update(changes)
        problems = self.problems(values)
        errors = self.blocking(problems, changes)
        if errors:
            raise SettingsError("; ".join(f"{p.key}: {p.text}" for p in errors))
        try:
            for key, value in changes.items():
                if self.values.get(key) != value:
                    self.env.set(key, value, system=self.schema[key].section == "system")
            self.env.save()
        except EnvError as err:
            raise SettingsError(str(err))
        return [p for p in problems if p.key in changes]


def load(repo_dir: str = REPO_DIR, schema_path: str = envschema.SCHEMA_PATH) -> Settings:
    path = os.path.join(repo_dir, ".env")
    try:
        env = EnvFile(path)
    except OSError as err:
        raise SettingsError(f"cannot read {path}: {err}")
    schema = envschema.load_schema(schema_path)
    services = envschema.compose_services(ops.compose_path(repo_dir, env.values()))
    return Settings(path, env, schema, services)


def shown(rule: envschema.Rule, value: str, reveal: bool = False) -> str:
    if rule.secret and not reveal:
        return envschema.mask(value)
    return value
