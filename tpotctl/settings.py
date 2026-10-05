"""The settings in ~/tpotce/.env: list, check and change them against the schema.

Used by `tpot env` and the Settings page of the menu. A change is only written if
the changed keys are valid afterwards; problems of keys nobody touched do not block
it, tpotinit reports those on the next start anyway.
"""

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

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
    # linux, mac or win of the host (ops.host_ostype); None: TPOT_OSTYPE is not compared with it
    host_ostype: Optional[str] = None

    @property
    def values(self) -> Dict[str, str]:
        return self.env.values()

    def relevant(self, include_all: bool = False, values: Optional[Dict[str, str]] = None,
                 offered: bool = False) -> List[envschema.Rule]:
        """The keys that matter for this type of T-Pot and these services, in schema order;
        offered adds the keys the schema offers anyway (the LLM settings)."""
        values = self.values if values is None else values
        return [rule for rule in self.schema.values()
                if include_all or envschema.applies(rule, values, self.services) or (offered and rule.offer)]

    def not_here(self, rule: envschema.Rule) -> str:
        """Why a shown key does not apply to this T-Pot, "" if it does."""
        if rule.scope and rule.scope != self.values.get("TPOT_TYPE", ""):
            return f"only on a {rule.scope}"
        if self.absent(rule):
            return "not in your edition"
        return ""

    def absent(self, rule: envschema.Rule) -> bool:
        """A key of a service that is not in the edition."""
        return bool(rule.services) and not set(rule.services) & set(self.services)

    def problems(self, values: Optional[Dict[str, str]] = None, offered: bool = False) -> List[envschema.Problem]:
        """The problems tpotinit finds; offered adds the ones of offered keys of absent services, as
        warnings: tpotinit does not check them."""
        values = self.values if values is None else values
        found = envschema.validate(values, self.services, self.schema)
        for key, value in self.fixes(values).items():
            found.append(envschema.Problem(
                "error", key, f"is {values.get(key, 'linux')}, this host runs {ops.OSTYPE_TEXT[value]}: "
                              f"tpotinit does not start, set it to {value}"))
        if offered:
            defaults = {key: rule.default for key, rule in self.schema.items()}
            for rule in self.schema.values():
                if rule.offer and self.absent(rule):
                    found += [envschema.Problem("warning", p.key, p.text)
                              for p in envschema.check(rule, values, defaults)]
        return found

    def fixes(self, values: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """{key: value} for fixed keys that do not match this host (TPOT_OSTYPE): tpotinit would
        not start, and the value that fits may be written without an unlock."""
        values = self.values if values is None else values
        current = values.get("TPOT_OSTYPE", "linux")
        if self.host_ostype in ops.OSTYPE_TEXT and current != self.host_ostype:
            return {"TPOT_OSTYPE": self.host_ostype}
        return {}

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

    def can_unlock(self, key: str) -> bool:
        """A fixed key with a warning in the schema; the managed ones have their own commands."""
        rule = self.schema.get(key)
        return rule is not None and not rule.editable and bool(rule.unlock) and key not in MANAGED_BY

    def change(self, changes: Dict[str, str], unlocked: Iterable[str] = ()) -> List[envschema.Problem]:
        """Write the changes, give back the warnings that remain; SettingsError if not written.
        A fixed key is only written if it is in unlocked (and can be unlocked)."""
        unlocked = set(unlocked)
        fixes = self.fixes()
        for key, value in changes.items():
            rule = self.schema.get(key)
            if rule is None:
                raise SettingsError(f"{key} is not a T-Pot setting, see tpot env list --all")
            if fixes.get(key) == value:
                continue
            if not rule.editable and not (key in unlocked and self.can_unlock(key)):
                if key in fixes:
                    hint = f", this host needs: tpot env set {key}={fixes[key]}"
                elif self.can_unlock(key):
                    hint = f", unlock it with: tpot env set --unlock {key}=..."
                else:
                    hint = ""
                raise SettingsError(f"{key} is {self.why_fixed(key)}, tpot does not change it{hint}")
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


def load(repo_dir: str = REPO_DIR, schema_path: str = envschema.SCHEMA_PATH,
         host_ostype: Optional[str] = None) -> Settings:
    path = os.path.join(repo_dir, ".env")
    try:
        env = EnvFile(path)
    except OSError as err:
        raise SettingsError(f"cannot read {path}: {err}")
    schema = envschema.load_schema(schema_path)
    services = envschema.compose_services(ops.compose_path(repo_dir, env.values()))
    return Settings(path, env, schema, services, host_ostype)


def shown(rule: envschema.Rule, value: str, reveal: bool = False) -> str:
    if rule.secret and not reveal:
        return envschema.mask(value)
    return value
