import calendar
import dataclasses
import json
import os
import random
import re
import unittest

import subprocess
import sys

from tests.helpers import DIST
from tpot_personas import catalog, servers
from tpot_personas.identity import (DAY, draw, first_install, host_patterns, hostname_regex, is_fresh, is_medium,
                                    kernel_time, is_firmware, log_window, ready_at, restore, stale_at, superseded_at,
                                    persona_digest, to_json, to_state, updated_at)

NOW = 1_790_000_000  # fixed "now" for reproducible tests


class IdentityTest(unittest.TestCase):
    def test_same_seed_same_identity(self):
        p = catalog.PERSONAS[0]
        self.assertEqual(draw(p, 7, NOW), draw(p, 7, NOW))

    def test_dates_are_ordered_and_never_in_the_future(self):
        for p in catalog.PERSONAS:
            for seed in range(200):
                i = draw(p, seed, NOW)
                self.assertLessEqual(i.installed_at, i.booted_at, (p.id, seed))
                self.assertLess(i.booted_at, NOW, (p.id, seed))
                self.assertGreaterEqual(i.boot_offset, DAY, (p.id, seed))
                limit = 400 if p.kind == "device" else 120
                self.assertLessEqual(i.boot_offset, (limit + 1) * DAY, (p.id, seed))
                self.assertGreaterEqual(NOW - i.installed_at, 90 * DAY, (p.id, seed))
                self.assertLessEqual(NOW - i.installed_at, p.max_install_days * DAY, (p.id, seed))
                # a system is not installed before its release or image existed
                self.assertGreaterEqual(i.installed_at, first_install(i.release), (p.id, seed))
                # it runs the kernel it booted, so that kernel was built (and the update made) before the boot
                self.assertLessEqual((kernel_time(i.release) or 0) + DAY, i.booted_at, (p.id, seed))
                self.assertLessEqual(ready_at(i.release) + DAY, i.booted_at, (p.id, seed))

    def test_ordering_holds_for_any_clock(self):
        # a clock long before the releases (or far after them) must still give ordered dates
        for now in (1_500_000_000, NOW, 2_200_000_000):
            for p in catalog.PERSONAS:
                for seed in range(30):
                    i = draw(p, seed, now)
                    self.assertLessEqual(i.installed_at, i.booted_at, (p.id, now, seed))
                    self.assertGreaterEqual(i.boot_offset, DAY, (p.id, now, seed))
                    self.assertGreaterEqual(now - i.installed_at, 90 * DAY, (p.id, now, seed))
                    self.assertLessEqual(now - i.installed_at, p.max_install_days * DAY, (p.id, now, seed))

    def test_hardware_tokens_in_host_names_match_the_hardware(self):
        p = catalog.by_id("ubuntu-noble")
        for seed in range(200):
            i = draw(p, seed, NOW)
            if i.hostname.startswith("ubuntu-s-"):
                gib = max(1, round(i.hardware.mem_kb / 1024 / 1024))
                self.assertTrue(i.hostname.startswith(f"ubuntu-s-{i.hardware.cpus}vcpu-{gib}gb-"), i.hostname)
                break
        else:
            self.fail("no droplet host name in 200 seeds")

    def test_raspberry_pi_kernel_fits_the_board(self):
        p = catalog.by_id("raspios")
        for seed in range(50):
            i = draw(p, seed, NOW)
            flavor = "rpi-2712" if "Pi 5" in i.hardware.board else "rpi-v8"
            self.assertTrue(i.release.kernel_version.endswith(flavor), (seed, i.release.kernel_version))
            self.assertNotIn("{", i.release.kernel_version)

    def test_hostname_tokens_are_expanded(self):
        for p in catalog.PERSONAS:
            for seed in range(50):
                host = draw(p, seed, NOW).hostname
                self.assertRegex(host, r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$", (p.id, host))
                self.assertNotIn("{", host)

    def test_seeds_vary_the_identity(self):
        p = catalog.by_id("ubuntu-jammy")
        seen = {(draw(p, s, NOW).boot_offset, draw(p, s, NOW).installed_at) for s in range(20)}
        self.assertGreater(len(seen), 15)

    def test_memory_has_a_generator_of_its_own(self):
        # random.Random(ident.seed) is the stream draw() takes the identity from
        from tpot_personas.common import mem_stats, rng_for
        for persona in catalog.PERSONAS[:4]:
            ident = draw(persona, 11, NOW)
            same = random.Random(ident.seed)
            total = ident.hardware.mem_kb
            old = sorted(int(total * same.uniform(0.20, 0.70)) for _ in range(2))
            mine = rng_for(ident, "mem")
            new = sorted(int(total * mine.uniform(0.20, 0.70)) for _ in range(2))
            stats = mem_stats(ident)
            self.assertEqual([stats["free"], stats["available"]], new, persona.id)
            self.assertNotEqual([stats["free"], stats["available"]], old, persona.id)

    def test_honeytokens_match_their_formats(self):
        # the format rules of the token kinds, the text around a token
        # as it stands in the persona's files (KEY=token, a line of its own)
        from tpot_personas.identity import _token
        rules = {
            "hf": (r"""(?:^|[\\'"`\s>=:(,)])(hf_(?i:[a-z]{34}))(?:$|[\\'"`\s<),])""",
                   r"""\b(?:hf_|api_org_)[a-zA-Z0-9]{34}\b"""),
            "openai": (r"""\b(sk-(?:proj|svcacct|admin)-(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})T3BlbkFJ"""
                       r"""(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})\b|sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20})"""
                       r"""(?:[\x60'"\s;]|\\[nr]|$)""",
                       r"""\b(sk-(?:(?:proj|svcacct|service)-[A-Za-z0-9_-]+|[a-zA-Z0-9]+)T3BlbkFJ[A-Za-z0-9_-]+)\b"""),
            "aws": (r"""\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\b""",
                    r"""\b((?:AKIA|ABIA|ACCA)[A-Z0-9]{16})\b"""),
        }
        for kind, rules in rules.items():
            for seed in range(300):
                token = _token(kind, random.Random(seed))
                for text in (f"KEY={token}\n", f"{token}\n", f'"{token}"'):
                    for rule in rules:
                        found = re.search(rule, text)
                        self.assertIsNotNone(found, (kind, rule, token))
                        self.assertIn(token, found.group(0), (kind, token))
        self.assertEqual(len(_token("openai", random.Random(1))), 164)
        self.assertNotEqual(_token("hf", random.Random(1)), _token("hf", random.Random(2)))

    def test_to_json_names_persona_and_boot(self):
        p = catalog.PERSONAS[0]
        ident = draw(p, 3, NOW)
        data = to_json(ident)
        self.assertEqual(data["persona"], p.id)
        self.assertEqual(data["booted_at"], NOW - ident.boot_offset)
        self.assertIsInstance(data["release"], dict)
        self.assertIsInstance(data["hardware"], dict)


class ProviderTest(unittest.TestCase):
    """Host name, kernel and hardware describe the same system about where the system runs."""

    def test_host_names_fit_the_provider_of_the_hardware(self):
        for p in catalog.PERSONAS:
            for seed in range(2000 if p.kind == "server" else 50):
                i = draw(p, seed, NOW)
                patterns = host_patterns(p, i.hardware)
                self.assertTrue(any(re.fullmatch(hostname_regex(x, i.hardware), i.hostname) for x in patterns),
                                (p.id, seed, i.hostname, i.hardware.provider))

    def test_noble_on_aws_runs_the_aws_kernel(self):
        p = catalog.by_id("ubuntu-noble")
        seen = set()
        for seed in range(2000):
            i = draw(p, seed, NOW)
            aws = i.hardware.provider == "aws"
            seen.add(i.hardware.provider)
            self.assertEqual(i.release.kernel_version.endswith("-aws"), aws, (seed, i.release.label))
            self.assertEqual(i.hostname.startswith("ip-172-31-"), aws, (seed, i.hostname))
            if i.hostname.startswith("ubuntu-s-"):
                self.assertEqual(i.hardware.provider, "do", seed)
        self.assertEqual(seen, {"aws", "kvm", "do"})

    def test_dgx_names_only_on_the_dgx(self):
        p = catalog.by_id("gpu-ubuntu")
        for seed in range(2000):
            i = draw(p, seed, NOW)
            if i.hostname.startswith(("dgx", "a100-")):
                self.assertEqual(i.hardware.gpus, ("NVIDIA A100-SXM4-80GB",) * 8, (seed, i.hostname))


class PatchLevelTest(unittest.TestCase):
    """Logged package versions are the current ones on the logged day."""

    def test_installed_before_the_next_patch_level_unless_from_its_image(self):
        for p in catalog.PERSONAS:
            for seed in range(500 if p.kind == "server" else 20):
                i = draw(p, seed, NOW)
                if stale_at(i.release) and not is_medium(i.release):
                    self.assertLessEqual(i.installed_at, stale_at(i.release), (p.id, seed, i.release.label))

    def test_log_window_is_the_time_the_versions_were_current(self):
        for p in catalog.PERSONAS:
            for seed in range(500 if p.kind == "server" else 20):
                i = draw(p, seed, NOW)
                window = log_window(i)
                if window is None:
                    # only an image install after the next patch level logs nothing
                    self.assertTrue(is_medium(i.release), (p.id, seed))
                    self.assertGreater(i.installed_at, stale_at(i.release), (p.id, seed))
                    continue
                first, last = window
                self.assertGreaterEqual(first, i.installed_at)
                if stale_at(i.release):
                    self.assertLessEqual(last, stale_at(i.release), (p.id, seed))

    def test_installation_image_level_stays_reachable(self):
        p = catalog.by_id("debian-bookworm")
        banners = {draw(p, seed, NOW).release.ssh_banner for seed in range(500)}
        self.assertIn("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u1", banners)
        self.assertIn("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2", banners)

    def test_every_server_draws_at_least_three_patch_levels(self):
        for p in catalog.PERSONAS:
            if p.kind == "server":
                labels = {draw(p, seed, NOW).release.label for seed in range(500)}
                self.assertGreaterEqual(len(labels), 3, (p.id, labels))


class StaleTest(unittest.TestCase):
    """No package log entry pulls a level's versions after they went stale."""

    def entries(self, ident):
        found = []
        for purpose in ("apt-history", "dnf-log", "yum-log"):
            found += [start + took for _, _, start, took in servers._log_entries(ident, purpose)]
        return found

    def test_no_log_entry_after_the_stale_day(self):
        for p in catalog.PERSONAS:
            for seed in range(1500 if p.kind == "server" else 20):
                i = draw(p, seed, NOW)
                stale = stale_at(i.release)
                for when in self.entries(i):
                    self.assertGreaterEqual(when, i.installed_at, (p.id, seed))
                    if stale:
                        self.assertLessEqual(when, stale, (p.id, seed, i.release.label))

    def test_installation_image_level_logs_nothing_later(self):
        later = calendar.timegm((2024, 7, 1, 0, 0, 0))  # date of openssh 1:9.2p1-2+deb12u3
        p = catalog.by_id("debian-bookworm")
        drawn = 0
        for seed in range(3000):
            i = draw(p, seed, NOW)
            if i.release.ssh_banner.endswith(("deb12u1", "deb12u2")):
                drawn += 1
                self.assertTrue(all(when < later for when in self.entries(i)), (seed, i.release.label))
        self.assertGreater(drawn, 500)

    def test_every_level_is_stale_at_most_90_days_after_its_update(self):
        for p in catalog.PERSONAS:
            for r in p.releases:
                if updated_at(r) and not is_firmware(r):
                    self.assertLessEqual(stale_at(r), updated_at(r) + 90 * DAY, (p.id, r.label))
                    self.assertGreater(stale_at(r), updated_at(r), (p.id, r.label))

    def test_gpu_levels_go_stale_with_their_ubuntu_levels(self):
        by_kernel = {r.kernel_version: r for pid in ("ubuntu-noble", "ubuntu-jammy")
                     for r in catalog.by_id(pid).releases}
        for r in catalog.by_id("gpu-ubuntu").releases:
            self.assertEqual(stale_at(r), stale_at(by_kernel[r.kernel_version]), r.label)

    def test_servers_draw_three_levels_now_and_later(self):
        for p in catalog.PERSONAS:
            if p.kind != "server":
                continue
            for days in (0, 60, 400):
                labels = {draw(p, seed, NOW + days * DAY).release.label for seed in range(600)}
                self.assertGreaterEqual(len(labels), 3, (p.id, days, labels))


class FirmwareTest(unittest.TestCase):
    """A device runs the firmware it got when it was set up (or the last one of
    its vendor) and is up to 400 days; its levels stay reachable for years."""

    def devices(self):
        return [p for p in catalog.PERSONAS if p.kind == "device"]

    def test_devices_draw_two_levels_now_and_later(self):
        for p in self.devices():
            for days in (0, 60, 400):
                labels = {draw(p, seed, NOW + days * DAY).release.label for seed in range(400)}
                self.assertGreaterEqual(len(labels), 2, (p.id, days, labels))

    def test_the_firmware_existed_when_the_device_was_set_up(self):
        for p in self.devices():
            for days in (0, 400):
                for seed in range(150):
                    i = draw(p, seed, NOW + days * DAY)
                    self.assertGreaterEqual(i.installed_at, first_install(i.release), (p.id, seed))
                    # never updated since: set up before the vendor published the next firmware
                    if superseded_at(i.release):
                        self.assertLessEqual(i.installed_at, superseded_at(i.release), (p.id, seed))
                    self.assertGreater(i.booted_at, ready_at(i.release), (p.id, seed))

    def test_firmware_goes_stale_with_the_next_firmware_only(self):
        for p in self.devices():
            for r in p.releases:
                self.assertTrue(is_firmware(r), (p.id, r.label))
                self.assertEqual(stale_at(r), superseded_at(r), (p.id, r.label))

    def test_vendor_host_names(self):
        expected = {
            "dlink-dir859": r"dlinkrouter|DIR-859",
            "tplink-wr841n": r"TL-WR841N",
            "zyxel-nas326": r"NAS326",
            "openwrt": r"OpenWrt",
            "qnap-qts": r"NAS[0-9A-F]{6}",
            "synology-dsm": r"DiskStation|DS1618|DS920",
            "ubiquiti-edgerouter-x": r"ubnt|EdgeRouter-X-5-Port",
            "ipcam": r"IPCamera|HIKVISION|cam-\d{2}",
        }
        for pid, pattern in expected.items():
            p = catalog.by_id(pid)
            names = {draw(p, seed, NOW).hostname for seed in range(200)}
            for name in names:
                self.assertRegex(name, rf"^(?:{pattern})$", pid)

    def test_synology_model_names_fit_the_hardware(self):
        p = catalog.by_id("synology-dsm")
        for seed in range(300):
            i = draw(p, seed, NOW)
            if i.hostname == "DS1618":
                self.assertIn("C3538", i.hardware.cpu_model, seed)
            if i.hostname == "DS920":
                self.assertIn("J4125", i.hardware.cpu_model, seed)


class DigestTest(unittest.TestCase):
    def test_digest_follows_the_catalog_data(self):
        p = catalog.by_id("ubuntu-noble")
        digests = {persona_digest(q) for q in catalog.PERSONAS}
        self.assertEqual(len(digests), len(catalog.PERSONAS))
        for change in ({"hostnames": p.hostnames + ("extra-{n}",)}, {"releases": p.releases[:-1]},
                       {"remove_paths": p.remove_paths + ("etc/extra",)}, {"max_install_days": 901}):
            with self.subTest(next(iter(change))):
                self.assertNotEqual(persona_digest(dataclasses.replace(p, **change)), persona_digest(p))

    def test_a_state_of_changed_catalog_data_is_refused(self):
        p = catalog.by_id("ubuntu-noble")
        state = to_state(p, draw(p, 5, NOW))
        changed = dataclasses.replace(p, hostnames=p.hostnames + ("extra-{n}",))
        with self.assertRaisesRegex(ValueError, "other data of ubuntu-noble"):
            restore(changed, state, NOW + DAY)

    def test_digest_is_the_same_in_every_process(self):
        # no set or address in the data: the hash seed of a process must not change it
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from tpot_personas import catalog; "
                "from tpot_personas.identity import persona_digest; "
                "print(' '.join(persona_digest(p) for p in catalog.PERSONAS))")
        out = {subprocess.run([sys.executable, "-c", code, str(DIST)], capture_output=True, text=True, check=True,
                              env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2")}
        self.assertEqual(len(out), 1)
        self.assertEqual(out.pop().split(), [persona_digest(p) for p in catalog.PERSONAS])


class RestoreTest(unittest.TestCase):
    """COWRIE_PERSONA_ROTATION: the identity of an earlier start, at a later now."""

    def setUp(self):
        self.persona = catalog.by_id("ubuntu-noble")
        self.first = draw(self.persona, 11, NOW)
        self.state = to_state(self.persona, self.first)

    def test_state_names_what_brings_the_identity_back(self):
        self.assertEqual(set(self.state), {"version", "persona", "digest", "seed", "chosen_at", "booted_at",
                                           "installed_at", "release_label", "hardware_index", "hostname"})
        self.assertEqual(self.state["digest"], persona_digest(self.persona))
        self.assertEqual(self.state["persona"], "ubuntu-noble")
        self.assertEqual(self.state["chosen_at"], NOW)
        self.assertEqual(self.state["booted_at"], self.first.booted_at)
        self.assertEqual(self.persona.hardware[self.state["hardware_index"]], self.first.hardware)
        self.assertEqual(json.loads(json.dumps(self.state)), self.state)

    def test_same_identity_with_a_grown_uptime(self):
        later = restore(self.persona, self.state, NOW + 3 * DAY + 5)
        for name in ("persona_id", "seed", "hostname", "release", "hardware", "installed_at", "pid_seed", "tokens",
                     "booted_at"):
            self.assertEqual(getattr(later, name), getattr(self.first, name), name)
        self.assertEqual(later.now, NOW + 3 * DAY + 5)
        self.assertEqual(later.boot_offset, self.first.boot_offset + 3 * DAY + 5)
        self.assertEqual(later.chosen_at, NOW)
        self.assertTrue(is_fresh(self.first))
        self.assertFalse(is_fresh(later))
        self.assertTrue(is_fresh(restore(self.persona, self.state, NOW)))

    def test_every_persona_comes_back(self):
        for p in catalog.PERSONAS:
            for seed in range(5):
                first = draw(p, seed, NOW)
                later = restore(p, to_state(p, first), NOW + 30 * DAY)
                self.assertEqual((later.hostname, later.release, later.hardware, later.booted_at, later.tokens),
                                 (first.hostname, first.release, first.hardware, first.booted_at, first.tokens),
                                 (p.id, seed))

    def test_a_state_that_does_not_fit_is_refused(self):
        cases = {
            "no object": [],
            "no version": {k: v for k, v in self.state.items() if k != "version"},
            "old version": {**self.state, "version": 0},
            "another persona": {**self.state, "persona": "ipcam"},
            "seed as text": {**self.state, "seed": "11"},
            "seed as bool": {**self.state, "seed": True},
            "negative boot": {**self.state, "booted_at": -1},
            "chosen after now": {**self.state, "chosen_at": NOW + 2 * DAY},
            "another release": {**self.state, "release_label": "other"},
            "another host name": {**self.state, "hostname": "other"},
            "another boot": {**self.state, "booted_at": self.state["booted_at"] + 1},
            "another installation": {**self.state, "installed_at": self.state["installed_at"] + 1},
            "hardware beyond the pool": {**self.state, "hardware_index": len(self.persona.hardware)},
            "no digest": {k: v for k, v in self.state.items() if k != "digest"},
            "other catalog data": {**self.state, "digest": "0" * 64},
        }
        for name, state in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                restore(self.persona, state, NOW + DAY)
