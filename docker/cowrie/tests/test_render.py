import calendar
import dataclasses
import json
import re
import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import catalog, tree, validate
from tpot_personas.identity import DAY, draw, fits, kernel_time, specialize, updated_at
from tpot_personas.render import render

NOW = 1_790_000_000


def render_ident(test, persona, ident):
    out = tmpdir(test) / persona.id
    keys = tmpdir(test) / "etc" / persona.id
    render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=keys)
    return ident, out, keys


def render_one(test, persona, seed, now=NOW):
    return render_ident(test, persona, draw(persona, seed, now))


class RenderTest(unittest.TestCase):
    def test_every_persona_renders_and_validates(self):
        for persona in catalog.PERSONAS:
            for seed in range(20):
                ident, out, keys = render_one(self, persona, seed)
                self.assertEqual(
                    validate.problems(persona, ident, out, out, keys, min_pickle=0), [],
                    (persona.id, seed))

    def test_identity_json_round_trips(self):
        persona = catalog.PERSONAS[0]
        ident, out, _ = render_one(self, persona, 3)
        data = json.loads((out / "identity.json").read_text())
        self.assertEqual(data["persona"], persona.id)
        self.assertEqual(data["hostname"], ident.hostname)
        self.assertEqual(data["boot_offset"], ident.boot_offset)

    def test_phil_is_gone(self):
        persona = catalog.PERSONAS[0]
        _, out, _ = render_one(self, persona, 1)
        self.assertNotIn(b"phil", (out / "fs.pickle").read_bytes().lower())

    def test_same_identity_same_output(self):
        persona = catalog.by_id("debian-bookworm")
        _, first, _ = render_one(self, persona, 9)
        _, second, _ = render_one(self, persona, 9)
        for rel in ("cmdoutput.json", "honeyfs/etc/shadow", "honeyfs/proc/meminfo", "txtcmds/usr/bin/top"):
            self.assertEqual((first / rel).read_text(), (second / rel).read_text(), rel)

    def test_restricted_directories_keep_owner_and_mode(self):
        # tree.ensure_file resets parent directories to 0:0 0755; the renderer
        # must put /root, /proc, /tmp and the home of the persona user back.
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 0)
            fs = tree.load_pickle(out / "fs.pickle")
            mode = lambda rel: tree.node_get(tree.find_node(fs, rel), tree.A_MODE) & 0o7777  # noqa: E731
            self.assertEqual(mode("root"), 0o700, persona.id)
            self.assertEqual(mode("proc"), 0o555, persona.id)
            self.assertEqual(mode("tmp"), 0o1777, persona.id)
            home = "root" if persona.user == "root" else f"home/{persona.user}"
            node = tree.find_node(fs, home)
            self.assertEqual((tree.node_get(node, tree.A_UID), tree.node_get(node, tree.A_GID)),
                             (persona.uid, persona.gid), persona.id)
            for path, child in tree.iter_nodes(node, "/" + home):
                self.assertEqual(tree.node_get(child, tree.A_UID), persona.uid, (persona.id, path))


class BoundaryTest(unittest.TestCase):
    """The edges of the identity ranges: every persona must still render and validate."""

    def check(self, persona, ident):
        _, out, keys = render_ident(self, persona, ident)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [],
                         (persona.id, ident.boot_offset, ident.installed_at, ident.now))

    def test_minimum_uptime_with_install_at_the_minimum_age(self):
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, NOW)
                self.check(persona, dataclasses.replace(ident, boot_offset=DAY, installed_at=NOW - 90 * DAY))

    def test_install_exactly_at_boot(self):
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, NOW)
                # the oldest kernel of the pool that fits, so the boot can be 100 days back
                oldest = min((r for r in persona.releases if fits(r, ident.hardware)), key=kernel_time)
                release = specialize(oldest, ident.hardware)
                self.check(persona, dataclasses.replace(ident, release=release, boot_offset=100 * DAY,
                                                        installed_at=NOW - 100 * DAY))

    def test_one_second_after_utc_midnight(self):
        midnight = NOW // DAY * DAY + 1
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, midnight)
                self.check(persona, ident)
                self.check(persona, dataclasses.replace(ident, boot_offset=DAY))


class ServerContentTest(unittest.TestCase):
    def test_gpu_tokens_differ_per_seed_and_land_in_files(self):
        persona = catalog.by_id("gpu-ubuntu")
        a, out_a, _ = render_one(self, persona, 1)
        b, _, _ = render_one(self, persona, 2)
        self.assertNotEqual(a.tokens["hf"], b.tokens["hf"])
        self.assertIn(a.tokens["hf"], (out_a / "honeyfs/home/ubuntu/.cache/huggingface/token").read_text())
        smi = (out_a / "txtcmds/usr/bin/nvidia-smi").read_text()
        self.assertEqual(smi.count(a.hardware.gpus[0]), len(a.hardware.gpus))

    def test_gpu_server_details(self):
        persona = catalog.by_id("gpu-ubuntu")
        for seed in range(12):
            ident, out, _ = render_one(self, persona, seed)
            r, hw = ident.release, ident.hardware
            smi = (out / "txtcmds/usr/bin/nvidia-smi").read_text()
            self.assertIn(f"Driver Version: {r.extra['nvidia_driver']}", smi)
            self.assertIn(f"CUDA Version: {r.extra['cuda']}", smi)
            self.assertEqual(smi.count(f"/  {hw.gpu_mem_mib}MiB"), len(hw.gpus))
            # every table line has the width of the nvidia-smi frame
            widths = {len(line) for line in smi.splitlines() if line.startswith(("|", "+"))}
            self.assertEqual(widths, {91}, seed)
            version = (out / "honeyfs/proc/driver/nvidia/version").read_text()
            self.assertIn(f"Kernel Module  {r.extra['nvidia_driver']}  ", version)
            lspci = (out / "txtcmds/usr/bin/lspci").read_text()
            self.assertEqual(len(re.findall(r"^\S+ (3D|VGA compatible) controller: NVIDIA Corporation", lspci, re.M)),
                             len(hw.gpus))
            ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
            commands = [p["COMMAND"] for p in ps]
            self.assertIn("/usr/local/bin/ollama serve", commands)
            self.assertIn("/opt/conda/bin/python /opt/conda/bin/jupyter-lab --no-browser --port=8888", commands)
            home = out / "honeyfs/home/ubuntu"
            env = (home / ".env").read_text()
            self.assertIn(f"OPENAI_API_KEY={ident.tokens['openai']}", env)
            self.assertIn(f"HF_TOKEN={ident.tokens['hf']}", env)
            history = (home / ".bash_history").read_text()
            for line in ("nvidia-smi", "ollama pull llama3.1:8b", "conda activate torch"):
                self.assertIn(line, history)

    def test_raspberry_pi_names_its_board(self):
        persona = catalog.by_id("raspios")
        for seed in range(8):
            ident, out, _ = render_one(self, persona, seed)
            board = ident.hardware.board
            self.assertEqual((out / "honeyfs/proc/device-tree/model").read_text(), board + "\0")
            cpuinfo = (out / "honeyfs/proc/cpuinfo").read_text()
            self.assertIn(f"Model\t\t: {board}", cpuinfo)
            self.assertNotIn("model name", cpuinfo)  # arm64 cpuinfo has none, lscpu names the core
            self.assertIn(ident.hardware.cpu_model, (out / "txtcmds/usr/bin/lscpu").read_text())
            issue = (out / "honeyfs/etc/rpi-issue").read_text()
            self.assertRegex(issue, r"^Raspberry Pi reference \d{4}-\d{2}-\d{2}\nGenerated using pi-gen, "
                                    r"https://github.com/RPi-Distro/pi-gen, [0-9a-f]{40}, stage4\n$")
            self.assertIn("arm_64bit=1", (out / "honeyfs/boot/firmware/config.txt").read_text())
            ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
            self.assertEqual(ps[0]["COMMAND"], "/sbin/init splash")
            self.assertTrue(any(p["COMMAND"].startswith("avahi-daemon: running") for p in ps))

    def test_centos_is_end_of_life_on_the_vault(self):
        persona = catalog.by_id("centos-7")
        ident, out, _ = render_one(self, persona, 4)
        for rel in ("etc/redhat-release", "etc/centos-release"):
            self.assertEqual((out / "honeyfs" / rel).read_text(), "CentOS Linux release 7.9.2009 (Core)\n")
        repo = (out / "honeyfs/etc/yum.repos.d/CentOS-Base.repo").read_text()
        self.assertIn("baseurl=http://vault.centos.org/7.9.2009/os/$basearch/", repo)
        self.assertNotIn("mirrorlist=", repo.replace("#mirrorlist=", ""))
        logs = sorted(p.name for p in (out / "honeyfs/var/log").iterdir() if p.name.startswith("yum.log"))
        self.assertTrue(logs and logs[0] == "yum.log", logs)
        ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.assertTrue(ps[0]["COMMAND"].startswith("/usr/lib/systemd/systemd "), ps[0])
        passwd = (out / "honeyfs/etc/passwd").read_text()
        self.assertIn("centos:x:1000:1000:Cloud User:/home/centos:/bin/bash", passwd)
        self.assertNotIn("www-data", passwd)

    def test_each_server_says_what_it_is(self):
        expected = {
            "ubuntu-noble": ("etc/os-release", 'VERSION_CODENAME=noble'),
            "ubuntu-jammy": ("etc/os-release", 'VERSION="22.04.5 LTS (Jammy Jellyfish)"'),
            "debian-trixie": ("etc/os-release", 'VERSION="13 (trixie)"'),
            "debian-bookworm": ("etc/os-release", 'VERSION="12 (bookworm)"'),
            "rhel-9": ("etc/redhat-release", "Red Hat Enterprise Linux release 9."),
            "centos-7": ("etc/os-release", 'PRETTY_NAME="CentOS Linux 7 (Core)"'),
            "raspios": ("etc/os-release", 'VERSION_CODENAME=bookworm'),
            "gpu-ubuntu": ("etc/os-release", "ID=ubuntu"),
        }
        for pid, (rel, text) in expected.items():
            _, out, _ = render_one(self, catalog.by_id(pid), 6)
            self.assertIn(text, (out / "honeyfs" / rel).read_text(), pid)
            version = (out / "honeyfs/proc/version").read_text()
            # a real /proc/version names the compiler between builder and build string
            self.assertRegex(version, r"^Linux version \S+ \(\S+@\S+\) \(.*(gcc|GCC).*\) #", pid)

    def test_upgrades_are_logged_between_the_update_and_the_boot(self):
        for pid in ("ubuntu-noble", "debian-bookworm", "rhel-9", "centos-7", "raspios"):
            persona = catalog.by_id(pid)
            for seed in range(10):
                ident, out, _ = render_one(self, persona, seed)
                for path in (out / "honeyfs/var/log").rglob("*"):
                    if not path.is_file():
                        continue
                    for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", path.read_text()):
                        day = calendar.timegm((int(y), int(m), int(d), 0, 0, 0))
                        self.assertGreaterEqual(day + DAY, updated_at(ident.release), (pid, seed, path.name))
