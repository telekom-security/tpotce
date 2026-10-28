"""openssl of the patched Cowrie (OPENSSL_COMMAND of patch_text_tools, in wc.py) and of the personas
(tpot_personas.system.openssl_data, the runtime file openssl.json): version with its options, help,
list -commands (OpenSSL 1.0.2: list-standard-commands and the other lists), the text and exit status of
an unknown command and the prompt of OpenSSL 1.x without an argument. The reference is the output of
the real openssl (tests/fixtures/openssl_refs.json): "builds" every build of a persona's release level
in a container of its release, "images" list and the prompt in containers of the release images, each
with the build of a persona that answers as it. The CPUINFO line of the builds is "CPUINFO: -" there
(the CPU of the persona decides it: test_cpuinfo_follows_the_cpu_of_the_persona)."""
import dataclasses
import json
import time
import unittest
from pathlib import Path

from tests.helpers import base_tree, load_data_pickle, tmpdir
from tests.test_patch import FakeConfig, module_all
from tests.test_text_tools import Base, Log, ShellFs, patched_root
from tpot_personas import catalog, firmware, render, servers, system, tree
from tpot_personas.identity import draw

REFS = json.loads((Path(__file__).parent / "fixtures" / "openssl_refs.json").read_text(encoding="utf-8"))
CLOSED = b""   # stdin of </dev/null

# name: (arguments, stdin) as the reference ran them (stdin None: the terminal of the probe, which no case reads)
CASES = {
    "version": (["version"], None), "version_a": (["version", "-a"], None),
    "version_aa": (["version", "-a", "-a"], None),
    **{f"version_{flag}": (["version", f"-{flag}"], None) for flag in "vbofpdemrcwx"},
    "version_av": (["version", "-av"], None), "version_bv": (["version", "-b", "-v"], None),
    "version_vb": (["version", "-v", "-b"], None), "version_x_v": (["version", "-x", "-v"], None),
    "version_dash": (["version", "-"], None), "version_dd": (["version", "--a"], None),
    "version_ddash": (["version", "--"], None), "version_ddash_extra": (["version", "--", "foo"], None),
    "version_ddx": (["version", "--x"], None), "version_dhelp": (["version", "--help"], None),
    "version_eq": (["version", "-a=1"], None), "version_extra": (["version", "extra"], None),
    "version_extra_v": (["version", "foo", "-v"], None), "version_v_extra": (["version", "-v", "foo"], None),
    "version_h": (["version", "-h"], None), "version_help": (["version", "-help"], None),
    "version_help_v": (["version", "-help", "-v"], None), "version_v_help": (["version", "-v", "-help"], None),
    "badcmd": (["foo"], None), "badcmd_args": (["nosuchcmd", "-x", "y"], None), "upper": (["VERSION"], None),
    "dash_h": (["-h"], None), "dash_v": (["-v"], None), "dashhelp": (["-help"], None),
    "ddhelp": (["--help"], None), "ddversion": (["--version"], None), "dversion": (["-version"], None),
    "help": (["help"], None), "help_version": (["help", "version"], None), "noargs": ([], CLOSED),
    "list_cmds": (["list", "-commands"], None), "interactive": ([], b"version\nfoo\nquit\n"),
    "list_standard": (["list-standard-commands"], None), "list_digest": (["list-message-digest-commands"], None),
    "list_cipher": (["list-cipher-commands"], None),
    "help_help": (["help", "-help"], None), "help_h": (["help", "-h"], None), "help_foo": (["help", "-foo"], None),
    "help_help_eq": (["help", "-help=1"], None), "version_help_eq": (["version", "-help=1"], None),
    "help_two": (["help", "version", "list"], None), "list_help_eq": (["list", "-help=1"], None),
    "list_noargs": (["list"], None), "list_extra": (["list", "x"], None), "list_foo": (["list", "-foo"], None),
    "list_cmds_x": (["list", "-commands", "-x"], None),
}


def text(value):
    return REFS["texts"][value[1:]] if isinstance(value, str) and value.startswith("@") else value


def recorded(entry):
    return tuple(text(value) for value in entry)


def build_data(build):
    """The openssl.json of a persona with this build (its CPUINFO "-" as the reference has it)."""
    return system.openssl_build_data(build, "-")


def levels(persona):
    ident = draw(persona, 3)
    for release in persona.releases:
        yield dataclasses.replace(ident, release=release)


class OpensslTest(unittest.TestCase):
    CONFIG = FakeConfig({})
    NAMESPACE = None

    def namespace(self, data):
        """The patched wc.py (compiled once), its [shell] openssl naming a file of data."""
        if OpensslTest.NAMESPACE is None:
            OpensslTest.NAMESPACE = module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                                               {"HoneyPotCommand": Base, "CowrieConfig": self.CONFIG, "log": Log,
                                                "tpot_shell_fs": ShellFs})
        path = tmpdir(self) / "openssl.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.CONFIG.values[("shell", "openssl")] = str(path)
        return OpensslTest.NAMESPACE

    def command(self, data, args, stdin=None):
        cmd = self.namespace(data)["commands"]["openssl"](list(args), stdin)
        cmd.start()
        return cmd

    def run_openssl(self, data, args, stdin=None):
        cmd = self.command(data, args, stdin)
        self.assertTrue(cmd.exited, f"openssl {args} did not exit")
        return cmd.out.decode("utf-8"), cmd.err, cmd.exit_code

    def test_the_commands_of_every_spelling_are_the_persona_s(self):
        commands = self.namespace({})["commands"]
        for spelling in ("openssl", "/usr/bin/openssl"):
            self.assertEqual(commands[spelling].__name__, "TpotOpenssl")
        # EdgeOS (Debian 9) has no merged /usr: /bin/openssl is no program there
        self.assertNotIn("/bin/openssl", commands)

    def test_every_case_answers_as_the_reference_openssl(self):
        """Each case gives the output, error text and exit status of the real openssl of its build."""
        runs = [(build, cases) for build, cases in REFS["builds"].items()]
        runs += [(image["build"], image["cases"]) for image in REFS["images"].values()]
        for build, cases in runs:
            data = build_data(build)
            for name, entry in cases.items():
                args, stdin = CASES[name]
                with self.subTest(build=build, case=name):
                    self.assertEqual(self.run_openssl(data, args, stdin), recorded(entry))

    def test_openssl_1_reads_its_commands_from_the_prompt(self):
        """Without an argument OpenSSL 1.x prompts for commands: from a pipe (the reference) and from the
        terminal, line by line, until quit or the end of the input; OpenSSL 3 prints its help."""
        for image in ("centos7", "stretch", "buster"):
            with self.subTest(image=image):
                build = REFS["images"][image]["build"]
                data = build_data(build)
                self.assertEqual(self.run_openssl(data, [], b"version\nfoo\nquit\n"),
                                 recorded(REFS["images"][image]["cases"]["interactive"]))
                version = data["lines"][0][1] + "\n"
                cmd = self.command(data, [])
                self.assertFalse(cmd.exited)
                self.assertEqual(cmd.out, b"OpenSSL> ")
                cmd.lineReceived("version")
                cmd.lineReceived("")
                self.assertEqual(cmd.out.decode(), "OpenSSL> " + version + "OpenSSL> OpenSSL> ")
                cmd.lineReceived("q")
                self.assertTrue(cmd.exited)
                self.assertEqual((cmd.err, cmd.exit_code), ("", 0))
                # the end of the input ends the prompt; a command that fails is named
                cmd = self.command(data, [])
                cmd.lineReceived("version -x")
                self.assertTrue(cmd.err.endswith("error in version\n"))
                cmd.eofReceived()
                self.assertTrue(cmd.exited)
                self.assertEqual(cmd.exit_code, 0)
        data = build_data(REFS["images"]["noble"]["build"])
        out, err, code = self.run_openssl(data, [], b"version\n")
        self.assertEqual((out, err, code), ("", text(REFS["images"]["noble"]["cases"]["interactive"][1]), 0))
        cmd = self.command(data, [])
        self.assertTrue(cmd.exited)

    def test_the_prompt_reads_the_stdin_of_an_exec_channel(self):
        """printf 'version\\nquit\\n' | ssh host openssl: the channel's stdin arrives with its end, on the
        protocol (as for the questions of rm -i, tests.test_fs_tools)."""
        data = build_data(REFS["images"]["buster"]["build"])
        cmd = self.command(data, [])
        cmd.protocol.execcmd = "openssl"
        cmd.protocol.input_data = b"version\nquit\n"
        self.assertFalse(cmd.exited)
        cmd.eofReceived()
        self.assertTrue(cmd.exited)
        version = data["lines"][0][1] + "\n"
        self.assertEqual((cmd.out.decode(), cmd.err, cmd.exit_code), ("OpenSSL> " + version + "OpenSSL> ", "", 0))

    def test_only_the_prompt_reads_the_stdin_of_the_shell(self):
        """openssl takes the stdin of its shell (printf 'openssl\\nversion\\n' | sh) only for the prompt of
        OpenSSL 1.x; the patched shell asks the command (patch_stdin_readers)."""
        cls = self.namespace(build_data(REFS["images"]["buster"]["build"]))["commands"]["openssl"]
        self.assertFalse(cls.consumes_stdin)
        self.assertTrue(cls.tpot_reads_stdin([]))
        self.assertFalse(cls.tpot_reads_stdin(["version"]))
        cls = self.namespace(build_data(REFS["images"]["noble"]["build"]))["commands"]["openssl"]
        self.assertFalse(cls.tpot_reads_stdin([]))
        shell = (patched_root() / "src/cowrie/shell/honeypot.py").read_text()
        self.assertIn('        reads = getattr(cmdclass, "tpot_reads_stdin", None)\n'
                      "        if reads(args) if reads is not None else cmdclass.consumes_stdin:\n"
                      "            self.stdin = None\n", shell)
        self.assertNotIn("        if cmdclass.consumes_stdin:\n", shell)

    def test_a_typed_line_is_logged(self):
        events = []
        data = build_data(REFS["images"]["centos7"]["build"])
        cmd = self.command(data, [])
        cmd.protocol.events.dispatch = lambda *args, **kwargs: events.append(kwargs)
        cmd.lineReceived("version -a")
        self.assertEqual(events, [{"realm": "openssl", "input": "version -a"}])


# --- the personas ----------------------------------------------------------------------

# the openssl package of each release level: the newest build of the level's OpenSSL published before its
#   update (Launchpad publishing history, snapshot.debian.org, archive.raspberrypi.com, the CentOS vault);
#   Red Hat: system.RHEL_OPENSSL of the minor release
PACKAGES = {
    ("ubuntu-noble", "2025-10-25"): "3.0.13-0ubuntu3.6", ("ubuntu-noble", "2026-02-25"): "3.0.13-0ubuntu3.7",
    ("ubuntu-noble", "2026-05-20"): "3.0.13-0ubuntu3.9", ("ubuntu-noble", "2026-07-20"): "3.0.13-0ubuntu3.11",
    ("ubuntu-jammy", "2025-05-20"): "3.0.2-0ubuntu1.19", ("ubuntu-jammy", "2025-12-15"): "3.0.2-0ubuntu1.20",
    ("ubuntu-jammy", "2026-04-20"): "3.0.2-0ubuntu1.23", ("ubuntu-jammy", "2026-08-25"): "3.0.2-0ubuntu1.26",
    ("gpu-ubuntu", "2025-12-15"): "3.0.2-0ubuntu1.20", ("gpu-ubuntu", "2026-04-20"): "3.0.2-0ubuntu1.23",
    ("gpu-ubuntu", "2026-02-25"): "3.0.13-0ubuntu3.7", ("gpu-ubuntu", "2026-07-20"): "3.0.13-0ubuntu3.11",
    ("debian-trixie", "2025-09-08"): "3.5.1-1", ("debian-trixie", "2025-11-20"): "3.5.4-1~deb13u1",
    ("debian-trixie", "2026-03-05"): "3.5.4-1~deb13u2", ("debian-trixie", "2026-05-20"): "3.5.6-1~deb13u1",
    ("debian-trixie", "2026-09-01"): "3.5.7-1~deb13u2",
    ("debian-bookworm", "2023-10-10"): "3.0.11-1~deb12u1", ("debian-bookworm", "2024-02-12"): "3.0.11-1~deb12u2",
    ("debian-bookworm", "2025-05-20"): "3.0.16-1~deb12u1", ("debian-bookworm", "2026-05-12"): "3.0.20-1~deb12u1",
    ("debian-bookworm", "2026-07-06"): "3.0.20-1~deb12u2",
    ("raspios", "2024-10-25"): "3.0.14-1~deb12u2+rpt1", ("raspios", "2025-01-30"): "3.0.15-1~deb12u1+rpt1",
    ("raspios", "2025-09-20"): "3.0.17-1~deb12u2+rpt1", ("raspios", "2026-03-15"): "3.0.18-1~deb12u2+rpt1",
    ("raspios", "2026-05-15"): "3.0.20-1~deb12u1",
    ("centos-7", "2020-11-12"): "1:1.0.2k-19.el7", ("centos-7", "2023-03-15"): "1:1.0.2k-25.el7_9",
    ("centos-7", "2023-12-13"): "1:1.0.2k-26.el7_9", ("centos-7", "2024-06-10"): "1:1.0.2k-26.el7_9",
}
# the devices whose firmware has an openssl program and the version it prints (the image of each release)
DEVICE_OPENSSL = {
    ("zyxel-nas326", "5.21(AAZF.14)C0"): "OpenSSL 1.0.2h  3 May 2016",
    ("zyxel-nas326", "5.21(AAZF.18)C0"): "OpenSSL 1.0.2h  3 May 2016",
    ("ubiquiti-edgerouter-x", "2.0.9-hotfix.2"): "OpenSSL 1.1.0l  10 Sep 2019",
    ("ubiquiti-edgerouter-x", "2.0.9-hotfix.7"): "OpenSSL 1.1.1n  15 Mar 2022",
}


class PersonaTest(unittest.TestCase):
    def test_servers_run_the_openssl_of_their_release(self):
        """openssl version prints the OpenSSL that ssh -V names; apt, yum and dnf have the package of its
        build installed; the program is /usr/bin/openssl."""
        seen = set()
        for persona in catalog.PERSONAS:
            if persona.kind != "server":
                continue
            for ident in levels(persona):
                key = (persona.id, ident.release.extra["updated"])
                with self.subTest(level=key):
                    data = system.openssl_data(persona, ident)
                    ssl = ident.release.ssh_version.split(", ", 1)[1]
                    self.assertEqual(data["lines"][0][1].split(" (Library: ")[0], ssl)
                    self.assertIn("usr/bin/openssl", system.programs(persona, ident))
                    if persona.family == "rhel":
                        rpm = servers.dnf_packages(persona, ident)
                        self.assertEqual(rpm["installed"]["openssl"]["evr"], data["package"])
                        self.assertEqual(data["package"], system.RHEL_OPENSSL[ident.release.os_version_id])
                        self.assertEqual(rpm["owners"]["/usr/bin/openssl"], "openssl")
                        continue
                    if persona.family == "centos":
                        installed = system.centos_installed(ident)
                    else:
                        installed = system.apt_packages(persona, ident)["installed"]
                    self.assertEqual(installed["openssl"], data["package"])
                    # the library packages of the same source build
                    for name in ("openssl-libs", "libssl3", "libssl3t64", "openssl-provider-legacy"):
                        if name in installed:
                            self.assertEqual(installed[name], data["package"], name)
                    self.assertEqual(data["package"], PACKAGES[key])
                    seen.add(key)
        self.assertEqual(seen, set(PACKAGES))

    def test_version_a_follows_the_build_of_the_package(self):
        cases = {("ubuntu-noble", "2025-10-25"): ("built on: Thu Sep 18 11:12:48 2025 UTC", "platform: debian-amd64"),
                 ("ubuntu-jammy", "2026-08-25"): ("built on: Wed Jul 29 16:56:04 2026 UTC", "platform: debian-amd64"),
                 ("debian-trixie", "2026-09-01"): ("built on: Sun Aug 23 15:26:22 2026 UTC", "platform: debian-amd64"),
                 ("debian-bookworm", "2023-10-10"): ("built on: Tue Sep 26 19:08:42 2023 UTC",
                                                     "platform: debian-amd64"),
                 ("raspios", "2025-01-30"): ("built on: Wed Nov  6 12:37:33 2024 UTC", "platform: debian-arm64"),
                 ("rhel-9", "2025-02-20"): ("built on: Tue Sep  3 00:00:00 2024 UTC", "platform: linux-x86_64"),
                 ("rhel-9", "2024-11-13"): ("built on: Thu Sep  5 00:00:00 2024 UTC", "platform: linux-x86_64"),
                 ("rhel-9", "2025-06-05"): ("built on: Wed Jan 29 00:00:00 2025 UTC", "platform: linux-x86_64"),
                 ("centos-7", "2024-06-10"): ("built on: reproducible build, date unspecified",
                                              "platform: linux-x86_64")}
        found = set()
        for persona in catalog.PERSONAS:
            for ident in levels(persona):
                key = (persona.id, ident.release.extra.get("updated"))
                if key in cases:
                    lines = dict(system.openssl_data(persona, ident)["lines"])
                    self.assertEqual((lines["b"], lines["p"]), cases[key], key)
                    found.add(key)
        self.assertEqual(found, set(cases))

    def test_no_build_is_newer_than_its_level(self):
        """The build date `version -a` names is not after the day of the level's update (a firmware: the
        day it came out)."""
        for persona in catalog.PERSONAS:
            for ident in levels(persona):
                data = system.openssl_data(persona, ident)
                if data is None:
                    continue
                built = dict(data["lines"])["b"].split(": ", 1)[1]
                if built.startswith("reproducible"):
                    continue
                day = time.strftime("%Y-%m-%d", time.strptime(built, "%a %b %d %H:%M:%S %Y UTC"))
                with self.subTest(persona=persona.id, level=ident.release.label):
                    self.assertLessEqual(day, ident.release.extra["updated"])

    def test_cpuinfo_follows_the_cpu_of_the_persona(self):
        """CPUINFO of OpenSSL 3: the capability words of the persona's CPU (x86: Intel or AMD, five words
        from 3.5 on; arm64: the Pi 4 without and the Pi 5 with the crypto extensions)."""
        def cpuinfo(persona_id, cpu_model):
            persona = catalog.by_id(persona_id)
            for ident in levels(persona):
                if ident.hardware.cpu_model == cpu_model:
                    return dict(system.openssl_data(persona, ident)["lines"])["c"]
            for hardware in persona.hardware:
                if hardware.cpu_model == cpu_model:
                    ident = dataclasses.replace(next(levels(persona)), hardware=hardware)
                    return dict(system.openssl_data(persona, ident)["lines"])["c"]
            raise AssertionError(cpu_model)
        self.assertEqual(cpuinfo("ubuntu-jammy", "Intel Xeon Processor (Icelake)"),
                         "CPUINFO: OPENSSL_ia32cap=0xfffa32034f8bfbff:0x405f5ef1bf27ab")
        self.assertEqual(cpuinfo("debian-bookworm", "AMD EPYC-Milan Processor"),
                         "CPUINFO: OPENSSL_ia32cap=0x7ef83203178bfbff:0x40069c219c97a9")
        self.assertEqual(cpuinfo("debian-trixie", "Intel Xeon Processor (Icelake)"),
                         "CPUINFO: OPENSSL_ia32cap=0xfffa32034f8bfbff:0x00405f5ef1bf27ab:0x0000000000000000"
                         ":0x0000000000000000:0x0000000000000000")
        self.assertEqual(cpuinfo("raspios", "Cortex-A72"), "CPUINFO: OPENSSL_armcap=0x81")
        self.assertEqual(cpuinfo("raspios", "Cortex-A76"), "CPUINFO: OPENSSL_armcap=0xbd")
        with self.assertRaisesRegex(KeyError, "Cortex-A53"):
            system.openssl_cpuinfo("arm", "Cortex-A53")

    def test_devices_have_openssl_where_their_firmware_has_it(self):
        found = set()
        for persona in catalog.PERSONAS:
            if persona.kind != "device":
                continue
            for ident in levels(persona):
                data = system.openssl_data(persona, ident)
                key = (persona.id, ident.release.label)
                with self.subTest(key=key):
                    content = persona.builder(persona, ident)
                    if key not in DEVICE_OPENSSL:
                        self.assertIsNone(data)
                        self.assertNotIn("usr/bin/openssl", system.programs(persona, ident))
                        self.assertIn("openssl", render.skip_python_commands(persona, ident, content))
                        continue
                    self.assertEqual(data["lines"][0][1], DEVICE_OPENSSL[key])
                    self.assertIn("usr/bin/openssl", system.programs(persona, ident))
                    self.assertNotIn("openssl", render.skip_python_commands(persona, ident, content))
                    found.add(key)
        self.assertEqual(found, set(DEVICE_OPENSSL))

    def test_the_rendered_persona_answers_openssl_as_its_data(self):
        """[shell] openssl names the persona's openssl.json; /usr/bin/openssl has the size of its build."""
        for persona in catalog.PERSONAS:
            ident = draw(persona, 5)
            with self.subTest(persona=persona.id):
                out = tmpdir(self)
                runtime = "/tmp/cowrie/persona"
                render.render(persona, ident, base_tree(), out, runtime, "/keys")
                cfg = (out / "cowrie.cfg").read_text()
                data = system.openssl_data(persona, ident)
                node = tree.find_node(load_data_pickle(out / "fs.pickle"), "usr/bin/openssl")
                if data is None:
                    self.assertNotIn("\nopenssl = ", cfg)
                    self.assertFalse((out / system.OPENSSL_DATA).exists())
                    # a program only where the program list of the firmware image has one (the Archer C7's,
                    #   whose version no recording has: it answers as the image's other programs)
                    self.assertEqual(node is not None, "usr/bin/openssl" in (firmware.programs(persona, ident) or {}))
                    continue
                self.assertIn(f"\nopenssl = {runtime}/{system.OPENSSL_DATA}\n", cfg)
                self.assertEqual(json.loads((out / system.OPENSSL_DATA).read_text()), json.loads(json.dumps(data)))
                self.assertEqual(node[tree.A_SIZE], system.openssl_size(system.openssl_build(persona, ident)))


if __name__ == "__main__":
    unittest.main()
