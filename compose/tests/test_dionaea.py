"""Dionaea's roots are read-only, /tmp a tmpfs and its resources limited in every compose file.

Uploads are kept in binaries/ (the upload quarantine), so the roots of the FTP,
TFTP, HTTP and UPnP services are only read: the services serve the files of T-Pot.

Run from the repository root: python3 -m unittest discover compose/tests
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("ftp", "tftp", "www", "upnp")
TARGET = "/opt/dionaea/var/dionaea/roots/"
# Dionaea at its memory limit is restarted with its container; the host keeps
# its memory and CPU for the rest
LIMITS = {"mem_limit": "1g", "memswap_limit": "1g", "pids_limit": 256, "cpus": 1}
# the files of the emulation (CreateFile, _lcreat: tempdownload_new() of dionaea) are
# made in /tmp; the container is read-only, without the tmpfs emulate:// samples are lost
TMP = "/tmp:uid=2000,gid=2000,size=16m"
# the storage limits of Dionaea: the variable of the fork, the key of .env and its default
# (the defaults of dionaea.cfg and store.yaml, which stay as they are)
LIMIT_ENV = {
    "DIONAEA_STREAMDUMPER_MAX_SIZE": ("DIONAEA_BISTREAMS_MAX_MB", "2048"),
    "DIONAEA_STREAMDUMPER_MAX_FILES": ("DIONAEA_BISTREAMS_MAX_FILES", "200000"),
    "DIONAEA_STORE_MAX_SIZE": ("DIONAEA_BINARIES_MAX_MB", "1024"),
}


def compose_files():
    return [ROOT / "docker-compose.yml", *sorted((ROOT / "compose").glob("*.yml")),
            ROOT / "docker/dionaea/docker-compose.yml"]


def volume_parts(volume):
    """Source, target and read-only flag of a short or long compose volume."""
    if isinstance(volume, dict):
        return volume.get("source", ""), volume.get("target", ""), bool(volume.get("read_only"))
    parts = str(volume).split(":")
    options = parts[2].split(",") if len(parts) > 2 else []
    return parts[0], parts[1] if len(parts) > 1 else "", "ro" in options


class DionaeaRootsTest(unittest.TestCase):

    def test_dionaea_does_not_allocate_stdin_or_tty(self):
        for path in compose_files():
            service = (yaml.safe_load(path.read_text()).get("services") or {}).get("dionaea")
            if service is None:
                continue
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                self.assertFalse(service.get("stdin_open", False))
                self.assertFalse(service.get("tty", False))

    def test_resource_limits(self):
        found = 0
        for path in compose_files():
            service = (yaml.safe_load(path.read_text()).get("services") or {}).get("dionaea")
            if service is None:
                continue
            found += 1
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                for key, value in LIMITS.items():
                    self.assertEqual(service.get(key), value, f"dionaea {key}")
        self.assertGreaterEqual(found, 7)

    def test_tmp_is_a_tmpfs(self):
        found = 0
        for path in compose_files():
            service = (yaml.safe_load(path.read_text()).get("services") or {}).get("dionaea")
            if service is None:
                continue
            found += 1
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                self.assertTrue(service.get("read_only"))
                self.assertIn(TMP, service.get("tmpfs") or [])
        self.assertGreaterEqual(found, 7)

    def test_roots_are_read_only(self):
        found = 0
        for path in compose_files():
            services = yaml.safe_load(path.read_text()).get("services") or {}
            if "dionaea" not in services:
                continue
            found += 1
            mounts = {}
            for volume in services["dionaea"].get("volumes", []):
                _source, target, read_only = volume_parts(volume)
                if target.startswith(TARGET):
                    mounts[target[len(TARGET):]] = read_only
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                self.assertEqual(sorted(mounts), sorted(ROOTS), "the four roots must be mounted")
                for name, read_only in sorted(mounts.items()):
                    self.assertTrue(read_only, f"roots/{name} is writable, it must end in :ro")
        # catalogue, standard, sensor, mobile, mac_win, root file, own file
        self.assertGreaterEqual(found, 7)

    def test_storage_limits_come_from_env(self):
        """The three limits are mapped from .env with the defaults of the config files."""
        found = 0
        for path in compose_files():
            service = (yaml.safe_load(path.read_text()).get("services") or {}).get("dionaea")
            if service is None:
                continue
            found += 1
            environment = service.get("environment") or []
            if isinstance(environment, dict):
                environment = [f"{k}={v}" for k, v in environment.items()]
            mapped = dict(item.split("=", 1) for item in environment)
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                for name, (key, default) in LIMIT_ENV.items():
                    self.assertEqual(mapped.get(name), "${%s:-%s}" % (key, default), name)

    def test_defaults_match_the_config_files(self):
        cfg = (ROOT / "docker/dionaea/dist/etc/dionaea.cfg").read_text()
        store = (ROOT / "docker/dionaea/dist/etc/ihandlers/store.yaml").read_text()
        self.assertRegex(cfg, r"(?m)^\s*config\.max_size=%s\s*$" % LIMIT_ENV["DIONAEA_STREAMDUMPER_MAX_SIZE"][1])
        self.assertRegex(cfg, r"(?m)^\s*config\.max_files=%s\s*$" % LIMIT_ENV["DIONAEA_STREAMDUMPER_MAX_FILES"][1])
        self.assertRegex(store, r"(?m)^\s*max_size:\s*%s\b" % LIMIT_ENV["DIONAEA_STORE_MAX_SIZE"][1])
        example = (ROOT / "env.example").read_text()
        for key, default in LIMIT_ENV.values():
            self.assertIsNotNone(re.search(r"(?m)^%s=%s$" % (key, default), example), key)


if __name__ == "__main__":
    unittest.main()
