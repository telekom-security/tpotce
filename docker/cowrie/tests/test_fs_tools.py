"""rm, cp, mv, touch, rmdir and mkdir of the patched Cowrie (MKDIR_COMMAND of patch_text_tools) against the
recordings of the real tools (fixtures/fs_tools.json): every case of every recorded image, run through the
patched wc.py on Cowrie's own file system (shell/fs.py of the patched fixture) set up as the recording was
(/tmp/w with the folders d (holding d/f), e and t, the empty files file, a and b, a root-owned /r). The
texts come from dist/tpot_personas/fs_texts.json (tools/build_fs_texts.py); a part `ls ...` of a case is
not run, what it printed is checked as the state of the file system."""
import ast
import calendar
import json
import posixpath
import re
import shlex
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

from tests.helpers import DIST
from tests.test_patch import FIXTURE, FakeConfig, module_all
from tests.test_text_tools import Base, Log, patched_root
from tpot_personas import catalog, system
from tpot_personas.identity import draw

HERE = Path(__file__).parent
REFS = json.loads((HERE / "fixtures" / "fs_tools.json").read_text(encoding="utf-8"))["images"]
TEXTS_PATH = DIST / "tpot_personas" / "fs_texts.json"
TEXTS = json.loads(TEXTS_PATH.read_text(encoding="utf-8"))
BUILDER = HERE.parent / "tools" / "build_fs_texts.py"
TOOLS = ("rm", "cp", "mv", "touch", "rmdir", "mkdir")
# cases the harness cannot model, with the reason
LEFT_OUT = {
    "rm -rf /": "as uid 1000 BusyBox walks the real root of the image (its /proc and /sys too): about 20000 "
                "lines, different from run to run, the recording keeps the first 20",
}
YEARS = {"8.22": "2013", "8.23": "2014", "8.26": "2016", "8.30": "2018", "8.32": "2020", "9.1": "2022",
         "9.4": "2023", "9.7": "2025"}
# the persona -> variant of fs_texts.json (system.fs_variant)
PERSONA_VARIANTS = {
    "ubuntu-noble": "ubuntu_24.04", "ubuntu-jammy": "ubuntu_22.04", "debian-trixie": "debian_13",
    "debian-bookworm": "debian_12", "rhel-9": "rockylinux_9", "centos-7": "centos_7", "raspios": "debian_12_en",
    "gpu-ubuntu": "ubuntu_22.04", "dlink-dir859": "fw_dlink_dir859_105b03", "tplink-archer-c7": "fw_tplink_c7v5",
    "zyxel-nas326": "fw_zyxel_nas326_aazf18", "openwrt": "fw_openwrt_21.02.7", "qnap-qts": "src_qnap_1.24.1",
    "synology-dsm": "debian_10", "ubiquiti-edgerouter-x": "fw_edgeos_209hf5", "ipcam": "fw_tplink_c7v5",
}
T_LINK, T_DIR, T_FILE = 0, 1, 2
NOW = 1_790_000_000


def shell_fs():
    """The module shell/fs.py of the patched fixture (HoneyPotFilesystem and its constants)."""
    class Logger:
        def __getattr__(self, name):
            return lambda *a, **k: None

    source = (patched_root() / "src/cowrie/shell/fs.py").read_text(encoding="utf-8")
    return module_all(source, {"Logger": Logger, "CowrieConfig": FakeConfig({}), "__name__": "tpot_fs"})


FS_MODULE = None


def fs_module():
    global FS_MODULE
    if FS_MODULE is None:
        FS_MODULE = shell_fs()
    return FS_MODULE


def node(name, kind, mode, contents=None, uid=0, ctime=NOW, target=None):
    return [name, kind, uid, uid, 4096 if kind == T_DIR else 0, mode, ctime,
            [] if contents is None else contents, target, None]


def make_fs(setup):
    """Cowrie's file system of a case: / with /bin, /tmp/w (cwd) and /r as the recording had them."""
    module = fs_module()
    fs = object.__new__(module["HoneyPotFilesystem"])
    w = node("w", T_DIR, 0o40755, [node("d", T_DIR, 0o40755, [node("f", T_FILE, 0o100644)]),
                                    node("e", T_DIR, 0o40755), node("t", T_DIR, 0o40755),
                                    node("file", T_FILE, 0o100644), node("a", T_FILE, 0o100644),
                                    node("b", T_FILE, 0o100644)])
    bin_dir = node("bin", T_DIR, 0o40755, setup.get("bin", []))
    root = node("/", T_DIR, 0o40755, [bin_dir, node("tmp", T_DIR, 0o41777, [w]),
                                      node("r", T_DIR, 0o40755, setup.get("r", [])),
                                      node("root", T_DIR, 0o40700)])
    fs.fs, fs._private_fs, fs.arch, fs.home = root, True, "linux-x64-lsb", "/root"
    fs.tempfiles, fs.filenames, fs.newcount, fs.generated_files = {}, {}, 0, {}
    return fs


TIME = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d):(\d\d|SS)(?:\.\d+)? ([+-]\d{4})|"
                  r"\w{3} (\w{3}) +(\d+) (\d\d:\d\d):(\d\d|SS) (\d{4})")


def stamp_of(line):
    """(wall clock 'YYYY-MM-DD HH:MM', seconds or None for SS, offset in seconds) of an ls -l line."""
    found = TIME.search(line)
    if found is None:
        return None
    if found.group(1):
        offset = int(found.group(3)[:3]) * 3600 + (1 if found.group(3)[0] == "+" else -1) * int(found.group(3)[3:]) * 60
        seconds = None if found.group(2) == "SS" else int(found.group(2))
        return found.group(1), seconds, offset
    wall = time.strftime("%Y-%m-%d", time.strptime(f"{found.group(8)} {found.group(4)} {found.group(5)}", "%Y %b %d"))
    seconds = None if found.group(7) == "SS" else int(found.group(7))
    return f"{wall} {found.group(6)}", seconds, 0


def epoch_of(stamp):
    wall, seconds, offset = stamp
    return calendar.timegm(time.strptime(wall, "%Y-%m-%d %H:%M")) + (seconds or 0) - offset


def keys_of(name, image):
    """[shell] keys of a recorded image: the variant's style, version, C library and BusyBox banner."""
    variant = TEXTS["variants"][name]
    keys = {"text_style": variant["style"], "text_version": variant["version"], "fs_texts": str(TEXTS_PATH),
            "fs_variant": name}
    if variant["style"] == "gnu":
        keys.update(text_year=YEARS[variant["version"]],
                    text_collate="posix" if image.get("locale") == "POSIX" else "c")
        if variant["version"] == "9.7":
            keys["text_packager"] = "Debian (9.7-3)"  # the line of Debian 13's coreutils (system.text_keys)
    else:
        keys.update(text_libc=variant["libc"], text_banner=variant["banner"])
    return keys


class Shell:
    """The patched wc.py of one variant, its commands run on one file system as the case's sh -c ran them."""

    def __init__(self, keys):
        config = FakeConfig({("shell", key): value for key, value in keys.items()})
        self.namespace = module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(encoding="utf-8"),
                                    {"HoneyPotCommand": Base, "CowrieConfig": config, "log": Log,
                                     "tpot_shell_fs": types.SimpleNamespace(**fs_module())})

    def run(self, fs, words, uid, stdin):
        cmd = self.namespace["commands"][words[0]](words[1:], stdin, fs)
        cmd.cwd = "/tmp/w"
        cmd.user = {"uid": uid, "gid": uid}
        Log.errors.clear()
        cmd.start()
        if Log.errors:
            raise AssertionError(f"internal error of {words}: {Log.errors}")
        if not cmd.exited:
            raise AssertionError(f"{words} did not exit")
        return cmd.out.decode("utf-8", "surrogateescape"), cmd.err, cmd.exit_code


def setup_of(case, result, uid):
    """The nodes a case needs beyond the base tree: /r/x or /r/y of the uid 1000 cases, /bin/sh of touch -r."""
    setup = {"r": [], "bin": []}
    if uid and case == "rm -f /r/x":
        setup["r"].append(node("x", T_FILE, 0o100644))
    if uid and case in ("rm /r/y", "rm -r /r", "rm -rf /r"):
        setup["r"].append(node("y", T_FILE, 0o100644))
    if uid and case.endswith("/r/w"):
        setup["r"].append(node("w", T_FILE, 0o100666))  # root's, writable by all: the time of now, not a given one
    if uid and case == "touch /r/v":
        setup["r"].append(node("v", T_FILE, 0o100644))
    if "touch -r /bin/sh" in case:
        lines = result[0].splitlines()
        sh = next((line for line in lines if line.endswith("/bin/sh") or " /bin/sh -> " in line), None)
        f4 = next((line for line in lines if line.endswith(" f4")), None)
        sh_time = epoch_of(stamp_of(sh)) if sh and stamp_of(sh) else NOW
        if sh and sh.startswith("l"):
            target = sh.rsplit(" -> ", 1)[1]
            # the program the link names: the time touch -r takes (it follows the link)
            setup["bin"] += [node("sh", T_LINK, 0o120777, ctime=sh_time, target=target),
                             node(target, T_FILE, 0o100755, ctime=epoch_of(stamp_of(f4)) if f4 else NOW - 7)]
        else:
            setup["bin"].append(node("sh", T_FILE, 0o100755, ctime=sh_time))
    else:
        setup["bin"].append(node("sh", T_FILE, 0o100755))
    return setup


def split_part(part):
    """(words, stdin, lines of head) of one command of a case: `CMD </dev/null`, `printf 'X' | CMD` (the
    answers of -i), `CMD | head -N`."""
    stdin, head = None, None
    if part.endswith(" </dev/null"):
        part, stdin = part[:-len(" </dev/null")], b""
    piped = re.fullmatch(r"printf '([^']*)' \| (.*)", part)
    if piped:
        part, stdin = piped.group(2), piped.group(1).encode().decode("unicode_escape").encode()
    found = re.fullmatch(r"(.*) \| head -(\d+)", part)
    if found:
        part, head = found.group(1), int(found.group(2))
    return shlex.split(part), stdin, head


class FsToolsTest(unittest.TestCase):
    maxDiff = None

    def check_state(self, fs, case, words, out, err):
        """What `ls` of a case printed: a path it could not access is missing, a listed one exists, the time
        a touch case set is the node's (a local time as wall clock, @epoch and -r as the instant)."""
        if "option" in err:
            return
        for name in [w for w in words[1:] if not w.startswith("-")]:
            path = posixpath.normpath(posixpath.join("/tmp/w", name))
            missing = any(name in line and "No such file" in line for line in err.splitlines())
            found = fs.getfile(path, follow_symlinks=False)
            self.assertEqual(found is None, missing, f"{case}: {path} {'exists' if found else 'is missing'}")
            if found is None or name == "/bin/sh":
                continue
            line = next((line for line in out.splitlines() if line.endswith(" " + name)), None)
            stamp = stamp_of(line) if line else None
            if stamp is None or not case.startswith("touch "):
                continue
            mine = found[fs_module()["A_CTIME"]]
            if "-d @" in case or "-r " in case:
                expected = epoch_of(stamp)
                if stamp[1] is None:
                    self.assertEqual(int(mine) // 60, expected // 60, case)
                else:
                    self.assertEqual(int(mine), expected, case)
            else:
                # a local time: the persona's clock runs in UTC, the wall clock is the one written
                self.assertEqual(time.strftime("%Y-%m-%d %H:%M", time.gmtime(mine)), stamp[0], case)
                # BusyBox takes the seconds of a time without them from the clock (the firmware recordings keep
                #   the seconds of their run)
                clock = not TEXTS["variants"][self.variant]["style"] == "gnu" and re.search(
                    r"-d '\d{4}-\d\d-\d\d \d\d:\d\d'", case)
                if stamp[1] is not None and not clock:
                    self.assertEqual(int(mine) % 60, stamp[1], case)

    def run_case(self, shell, case, result, uid, firmware=False):
        fs = make_fs(setup_of(case, result, uid))
        out, err, status, last = "", "", 0, 0
        parts = case.split("; ")
        for index, part in enumerate(parts):
            words, stdin, head = split_part(part)
            if words[0] == "ls":
                self.assertEqual(index, len(parts) - 1, case)
                self.assertTrue(result[0].startswith(out), f"{case}: stdout {out!r} / {result[0]!r}")
                self.assertTrue(result[1].startswith(err), f"{case}: stderr {err!r} / {result[1]!r}")
                self.check_state(fs, case, words, result[0][len(out):], result[1][len(err):])
                return None
            if words == ["echo", "$?"]:
                # the firmware recordings ran each call on its own: their `echo $?` keeps the status before it
                out, status = out + f"{last}\n", last if firmware else 0
                continue
            one_out, one_err, last = shell.run(fs, words, uid, stdin)
            if head is not None:
                one_out = "".join(one_out.splitlines(keepends=True)[:head])
                last = 0
            out, err, status = out + one_out, err + one_err, last
        return [out, err, status]

    def test_every_recorded_case(self):
        counts = {}
        for key, image in REFS.items():
            name = key.removesuffix("_user")
            uid = image["uid"]
            shell = Shell(keys_of(name, image))
            self.variant = name
            done = left = 0
            for case, result in image["cases"].items():
                if case in LEFT_OUT and uid and image["kind"].startswith("busybox"):
                    left += 1
                    continue
                done += 1
                with self.subTest(image=key, case=case):
                    found = self.run_case(shell, case, result, uid, key.startswith("fw_"))
                    if found is not None:
                        self.assertEqual(found, result)
            counts[key] = (done, left)
        self.assertEqual(sum(done for done, _ in counts.values()) + sum(left for _, left in counts.values()),
                         sum(len(image["cases"]) for image in REFS.values()))

    def test_the_texts_are_the_build_of_the_recordings(self):
        done = subprocess.run([sys.executable, "-I", str(BUILDER), "--check"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_every_upstream_spelling_is_the_persona_s_command(self):
        # the spellings upstream's fs.py registers for its rm, cp, mv, touch, rmdir and mkdir (read from the
        #   fixture, so a new one of a Cowrie update fails here); ">" stays upstream's touch (`> file`)
        tree = ast.parse((FIXTURE / "src/cowrie/commands/fs.py").read_text(encoding="utf-8"))
        spellings = {}
        for item in tree.body:
            if (isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Subscript)
                    and isinstance(item.value, ast.Name) and item.value.id in {f"Command_{t}" for t in TOOLS}):
                spellings.setdefault(item.value.id.removeprefix("Command_"), set()).add(
                    item.targets[0].slice.value)
        self.assertEqual(set(spellings), set(TOOLS))
        commands = Shell(keys_of("debian_12", {})).namespace["commands"]
        for tool, names in spellings.items():
            for spelling in (names - {">"}) | {f"/usr/bin/{tool}"}:
                with self.subTest(spelling=spelling):
                    self.assertEqual(commands[spelling].__name__, f"Command_tpot_{tool}")
        self.assertIn(">", spellings["touch"])
        self.assertNotIn(">", commands)

    def test_the_variant_of_every_persona(self):
        self.assertEqual({p.id for p in catalog.PERSONAS}, set(PERSONA_VARIANTS))
        for persona in catalog.PERSONAS:
            ident = draw(persona, 1, NOW)
            with self.subTest(persona.id):
                keys = system.fs_keys(persona, ident)
                self.assertEqual(keys["fs_variant"], PERSONA_VARIANTS[persona.id])
                self.assertEqual(Path(keys["fs_texts"]), TEXTS_PATH)
                self.assertIn(keys["fs_variant"], TEXTS["variants"])

    def test_openwrt_takes_the_variant_of_its_release(self):
        persona = next(p for p in catalog.PERSONAS if p.id == "openwrt")
        seen = {}
        for seed in range(40):
            ident = draw(persona, seed, NOW)
            seen[ident.release.label[:5]] = system.fs_variant(persona, ident)
        for label, variant in seen.items():
            self.assertEqual(variant, "fw_openwrt_21.02.7" if label.startswith("21.") else "fw_openwrt_22.03.7")

    def test_every_variant_is_recorded(self):
        for name, variant in TEXTS["variants"].items():
            self.assertNotIn("derived", variant["recorded"], name)
        dlink = TEXTS["variants"]["fw_dlink_dir859_105b03"]
        self.assertEqual((dlink["version"], dlink["missing"], dlink["long_options"]), ("1.14.1", ["rmdir"], False))
        self.assertNotIn("rmdir", dlink["usage"])
        self.assertNotIn("can't", "".join(dlink["templates"].values()))
        self.assertEqual(dlink["options"]["mv"], {"short": "fi", "long": {}})
        zyxel = TEXTS["variants"]["fw_zyxel_nas326_aazf18"]
        self.assertEqual((zyxel["libc"], zyxel["long_options"], zyxel["verbose"]["rm"], zyxel["help_status"]),
                         ("glibc", True, False, 1))
        self.assertEqual(zyxel["options"]["touch"]["short"], "cfma")
        qnap = TEXTS["variants"]["src_qnap_1.24.1"]
        self.assertEqual((qnap["long_options"], qnap["verbose"]["mkdir"], qnap["help_status"]), (True, True, 0))
        self.assertIn("d:", qnap["options"]["touch"]["short"])


# the templates the commands read, per style (a BusyBox variant needs those of -v, -d / -t and -r only where it
#   has the option and prints)
NEEDED = {
    "gnu": ("rm.missing_operand", "rm.cannot_remove", "rm.prompt", "rm.preserve_root", "rm.removed", "rm.removed_dir",
            "rm.descend", "rm.dot", "rm.prompt_some", "rm.prompt_some_r",
            "rm.prompt_some_r_one", "cp.missing_operand", "cp.missing_dest", "cp.target", "cp.extra", "cp.stat",
            "cp.omit_dir", "cp.same", "cp.into_itself", "cp.nondir", "cp.dir", "cp.create", "cp.mkdir",
            "cp.overwrite", "cp.verbose", "mv.missing_operand", "mv.missing_dest", "mv.target", "mv.extra",
            "mv.stat", "mv.same", "mv.subdir", "mv.nondir", "mv.dir", "mv.cannot_move", "mv.overwrite",
            "mv.verbose", "rmdir.missing_operand", "rmdir.failed", "rmdir.failed_parent", "rmdir.verbose",
            "touch.missing_operand", "touch.invalid_date", "touch.reference", "touch.cannot_touch",
            "touch.setting_times", "mkdir.cannot_create", "mkdir.invalid_mode", "mkdir.missing_operand",
            "mkdir.verbose"),
    "busybox": ("rm.cannot_remove", "rm.stat", "rm.is_dir", "rm.prompt", "rm.prompt_dir", "rm.descend", "rm.dot",
                "cp.stat", "cp.omit_dir", "cp.same", "cp.recursion", "cp.is_dir", "mv.is_dir", "cp.create", "cp.mkdir",
                "cp.overwrite", "mv.stat", "mv.rename", "mv.overwrite", "rmdir.failed", "touch.cannot_touch",
                "mkdir.cannot_create", "mkdir.invalid_mode"),
}
VERBOSE = {"rm": ("rm.removed", "rm.removed_dir"), "cp": ("cp.verbose",), "mv": ("mv.verbose",),
           "rmdir": ("rmdir.verbose",), "mkdir": ("mkdir.verbose",)}


class VariantTest(unittest.TestCase):
    def test_every_variant_has_the_texts_its_commands_read(self):
        for name, variant in TEXTS["variants"].items():
            with self.subTest(name):
                needed = set(NEEDED[variant["style"]])
                if variant["style"] == "busybox":
                    missing = set(variant.get("missing", ()))
                    for tool, keys in VERBOSE.items():
                        if tool not in missing and variant["verbose"][tool] and "v" in variant["options"][tool]["short"]:
                            needed.update(keys)
                    touch = variant["options"]["touch"]["short"]
                    needed.update(key for key, letter in (("touch.invalid_date", "d:"), ("touch.reference", "r:"))
                                  if letter in touch)
                    needed = {key for key in needed if key.split(".")[0] not in missing}
                    self.assertEqual(set(variant["usage"]), set(TOOLS) - missing)
                else:
                    self.assertEqual(set(variant["help"]), set(TOOLS))
                    self.assertEqual(set(variant["rm_kinds"]), {"empty", "file", "dir", "link"})
                self.assertEqual(needed - set(variant["templates"]), set())
                self.assertEqual(set(variant["options"]), set(TOOLS) - set(variant.get("missing", ())))


class BehaviourTest(unittest.TestCase):
    """What the recordings do not hold, as the sources of the tools have it: a file into a folder, the same
    file, -i with an answer, -v of a tree, a sticky folder, and the bounds of a command."""

    def run_in(self, name, line, uid=0, stdin=None, fs=None):
        shell = SHELLS.get(name) or SHELLS.setdefault(name, Shell(keys_of(name, {})))
        self.fs = fs or getattr(self, "fs", None) or make_fs(setup_of("", ["", "", 0], 0))
        return tuple(shell.run(self.fs, shlex.split(line), uid, stdin))

    def setUp(self):
        self.fs = make_fs(setup_of("", ["", "", 0], 0))

    def exists(self, path):
        return self.fs.getfile(path, follow_symlinks=False) is not None

    def test_a_file_into_a_folder(self):
        self.assertEqual(self.run_in("debian_12", "cp -v a t"), ("'a' -> 't/a'\n", "", 0))
        self.assertEqual(self.run_in("debian_12", "mv -v b t/"), ("renamed 'b' -> 't/b'\n", "", 0))
        self.assertTrue(self.exists("/tmp/w/t/a") and self.exists("/tmp/w/a") and self.exists("/tmp/w/t/b"))
        self.assertFalse(self.exists("/tmp/w/b"))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "cp -v file d"), ("'file' -> 'd/file'\n", "", 0))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "mv d e"), ("", "", 0))
        self.assertTrue(self.exists("/tmp/w/e/d/file") and not self.exists("/tmp/w/d"))

    def test_the_same_file(self):
        self.assertEqual(self.run_in("debian_12", "cp a a"), ("", "cp: 'a' and 'a' are the same file\n", 1))
        self.assertEqual(self.run_in("debian_12", "mv a ./a"), ("", "mv: 'a' and './a' are the same file\n", 1))
        self.assertEqual(self.run_in("centos_7", "cp a a"), ("", "cp: \u2018a\u2019 and \u2018a\u2019 are the same file\n", 1))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "cp a a"), ("", "cp: 'a' and 'a' are the same file\n", 1))
        self.assertEqual(self.run_in("debian_12", "cp -r d d"),
                         ("", "cp: cannot copy a directory, 'd', into itself, 'd/d'\n", 1))
        self.assertTrue(self.exists("/tmp/w/a"))

    def test_rm_takes_the_answer_of_a_pipe(self):
        self.assertEqual(self.run_in("debian_12", "rm -i a b", stdin=b"y\nn\n"),
                         ("", "rm: remove regular empty file 'a'? rm: remove regular empty file 'b'? ", 0))
        self.assertFalse(self.exists("/tmp/w/a"))
        self.assertTrue(self.exists("/tmp/w/b"))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -i b", stdin=b"yes\n"), ("", "rm: remove 'b'? ", 0))
        self.assertFalse(self.exists("/tmp/w/b"))

    def test_rm_v_of_a_tree(self):
        self.assertEqual(self.run_in("debian_12", "rm -rv d"), ("removed 'd/f'\nremoved directory 'd'\n", "", 0))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -rv e"), ("removed directory: 'e'\n", "", 0))
        self.assertEqual(self.run_in("fw_openwrt_22.03.7", "rm -rv t"), ("", "", 0))
        self.assertFalse(self.exists("/tmp/w/d") or self.exists("/tmp/w/e") or self.exists("/tmp/w/t"))

    def test_a_sticky_folder(self):
        # /tmp is 1777 and root's /tmp/w is in it: a user may not take it out, nor a file of root from it
        self.assertEqual(self.run_in("debian_12", "rm -rf /tmp/w", uid=1000)[2], 1)
        self.assertTrue(self.exists("/tmp/w/d/f"))
        self.assertEqual(self.run_in("debian_12", "mv /tmp/w /tmp/x", uid=1000),
                         ("", "mv: cannot move '/tmp/w' to '/tmp/x': Operation not permitted\n", 1))
        self.assertEqual(self.run_in("debian_12", "touch /tmp/mine", uid=1000), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/mine")[2], 1000)
        self.assertEqual(self.run_in("debian_12", "rm /tmp/mine", uid=1000), ("", "", 0))

    def test_touch_sets_the_time_of_an_existing_file(self):
        self.assertEqual(self.run_in("debian_12", "touch -d '2020-02-03 04:05:06' a"), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/w/a")[6], calendar.timegm((2020, 2, 3, 4, 5, 6)))
        self.assertEqual(self.run_in("debian_12", "touch -a -t 202101010000 a"), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/w/a")[6], calendar.timegm((2020, 2, 3, 4, 5, 6)))
        self.assertEqual(self.run_in("debian_12", "touch -d 2021-03-04T05:06:07 a"), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/w/a")[6], calendar.timegm((2021, 3, 4, 5, 6, 7)))
        self.assertEqual(self.run_in("debian_12", "touch -t 1312312359 b"), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/w/b")[6], calendar.timegm((2013, 12, 31, 23, 59, 0)))
        before = time.time()
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "touch file"), ("", "", 0))
        self.assertGreaterEqual(self.fs.getfile("/tmp/w/file")[6], before)
        self.assertEqual(self.run_in("fw_tplink_c7v5", "touch -c nothing"), ("", "", 0))
        self.assertFalse(self.exists("/tmp/w/nothing"))

    def test_touch_d_reads_the_forms_of_gnu_date(self):
        # recorded on centos:7, debian:12 and ubuntu:24.04 (LC_ALL=C TZ=UTC): the times of the fixed forms,
        #   status 0 for the relative ones
        for text, expected in (("20200101", (2020, 1, 1, 0, 0, 0)),
                               ("Mon Jan  1 00:00:00 UTC 2024", (2024, 1, 1, 0, 0, 0)),
                               ("2024-01-02 03:04:05 +0200", (2024, 1, 2, 1, 4, 5))):
            for name in ("centos_7", "debian_12", "ubuntu_24.04"):
                with self.subTest(name=name, text=text):
                    self.assertEqual(self.run_in(name, f"touch -d '{text}' a"), ("", "", 0))
                    self.assertEqual(self.fs.getfile("/tmp/w/a")[6], calendar.timegm(expected))
        day = 86400
        for text, offset in (("2 hours ago", -7200), ("1 day", day), ("3 weeks ago", -21 * day),
                             ("10 minutes", 600), ("-1 hour", -3600), ("tomorrow", day), ("day", day),
                             ("1 day ago 2 hours", -day + 7200)):
            with self.subTest(text=text):
                now = time.time()
                self.assertEqual(self.run_in("debian_12", f"touch -d '{text}' a"), ("", "", 0))
                self.assertAlmostEqual(self.fs.getfile("/tmp/w/a")[6], now + offset, delta=3)
        now = time.time()
        self.assertEqual(self.run_in("debian_12", "touch -d 'next monday' a"), ("", "", 0))
        when = self.fs.getfile("/tmp/w/a")[6]
        self.assertEqual((time.gmtime(when).tm_wday, when % day), (0, 0))
        self.assertTrue(now < when <= now + 7 * day)
        self.assertEqual(self.run_in("debian_12", "touch -d 'last fri' a"), ("", "", 0))
        when = self.fs.getfile("/tmp/w/a")[6]
        self.assertEqual((time.gmtime(when).tm_wday, when % day), (4, 0))
        self.assertTrue(now - 8 * day < when <= now)
        self.assertEqual(self.run_in("debian_12", "touch -d 'yesterday 12:00' a"), ("", "", 0))
        when = time.gmtime(self.fs.getfile("/tmp/w/a")[6])
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M:%S", when),
                         time.strftime("%Y-%m-%d", time.gmtime(now - day)) + " 12:00:00")
        self.assertEqual(self.run_in("debian_12", "touch -d '2024-01-31 1 month' a"), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/w/a")[6], calendar.timegm((2024, 3, 2, 0, 0, 0)))
        self.assertEqual(self.run_in("debian_12", "touch -d 'Jan 5, 2021 3:04 pm' a"), ("", "", 0))
        self.assertEqual(self.fs.getfile("/tmp/w/a")[6], calendar.timegm((2021, 1, 5, 15, 4, 0)))
        for text in ("2 lightyears ago", "2024-02-30", "25:00", "ago"):
            with self.subTest(text=text):
                self.assertEqual(self.run_in("debian_12", f"touch -d '{text}' a"),
                                 ("", f"touch: invalid date format \u2018{text}\u2019\n", 1))

    def test_touch_goes_on_with_the_next_name(self):
        # what upstream's tests of touch check (cowrie.test.test_rmdir_touch), with the persona's quotes
        proc = node("proc", T_DIR, 0o40555)
        self.fs.fs[7].append(proc)
        self.assertEqual(self.run_in("debian_12", "touch /nonexistent/f1 f2 /proc/x ok1"),
                         ("", "touch: cannot touch '/nonexistent/f1': No such file or directory\n"
                              "touch: cannot touch '/proc/x': Permission denied\n", 1))
        self.assertTrue(self.exists("/tmp/w/f2") and self.exists("/tmp/w/ok1"))
        self.assertFalse(self.exists("/proc/x"))

    def test_rm_no_preserve_root(self):
        self.assertEqual(self.run_in("debian_12", "rm -rf --no-preserve-root /")[2], 1)
        self.assertEqual(self.fs.getfile("/")[7], [])
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -rf /tmp/w/d")[2], 0)

    def test_the_operands_and_the_nodes_of_one_command_are_bounded(self):
        names = " ".join(f"n{i}" for i in range(5000))
        self.assertEqual(self.run_in("debian_12", "touch " + names), ("", "", 0))
        self.assertTrue(self.exists("/tmp/w/n4095"))
        self.assertFalse(self.exists("/tmp/w/n4096"))
        shell = SHELLS["debian_12"]
        shell.namespace["TPOT_FS_NODES"] = 50
        try:
            out, err, status = self.run_in("debian_12", "rm -rv /tmp/w")
            self.assertEqual(status, 1)
            self.assertLessEqual(out.count("\n"), 50)
            out, err, status = self.run_in("debian_12", "cp -r /tmp/w /tmp/copy")
            self.assertEqual(status, 1)
            self.assertTrue(self.exists("/tmp/copy"))
        finally:
            shell.namespace["TPOT_FS_NODES"] = 10_000

    def deep_tree(self, levels, uid):
        """/tmp/u of uid, holding a/a/.../a (levels folders) and the file f at its end."""
        top = at = node("u", T_DIR, 0o40755, uid=uid)
        for _ in range(levels):
            child = node("a", T_DIR, 0o40755, uid=uid)
            at[7].append(child)
            at = child
        at[7].append(node("f", T_FILE, 0o100644, uid=uid))
        self.fs.getfile("/tmp")[7].append(top)
        return "/a" * levels + "/f"

    def test_a_deep_tree(self):
        # the walks of rm and cp keep their folders on a stack: 1500 levels end as the real tools end
        for uid in (0, 1000):
            for name, line in (("debian_12", "rm -rf /tmp/u"), ("debian_12", "rm -rv /tmp/u"),
                               ("busybox_1.36.1-musl", "rm -r /tmp/u"), ("busybox_1.36.1-musl", "rm -rv /tmp/u")):
                with self.subTest(uid=uid, name=name, line=line):
                    self.fs = make_fs(setup_of("", ["", "", 0], 0))
                    self.deep_tree(1500, uid)
                    out, err, status = self.run_in(name, line, uid=uid)
                    self.assertEqual((err, status), ("", 0))
                    self.assertFalse(self.exists("/tmp/u"))
                    if "v" in line:
                        self.assertTrue(out.startswith("removed '/tmp/u/a/a/"), out[:40])
            for name in ("debian_12", "busybox_1.36.1-musl"):
                with self.subTest(uid=uid, name=name, line="cp -r"):
                    self.fs = make_fs(setup_of("", ["", "", 0], 0))
                    deep = self.deep_tree(1500, uid)
                    self.assertEqual(self.run_in(name, "cp -r /tmp/u /tmp/c", uid=uid), ("", "", 0))
                    self.assertTrue(self.exists("/tmp/c" + deep))
                    self.assertEqual(self.fs.getfile("/tmp/c" + deep)[2], uid)

    def test_cp_names_a_name_too_long(self):
        # cp reads and writes each node by its whole name: past PATH_MAX (4096 with its NUL) it is ENAMETOOLONG
        self.deep_tree(2100, 0)
        out, err, status = self.run_in("debian_12", "cp -r /tmp/u /tmp/ccc")
        self.assertEqual(status, 1)
        self.assertEqual(err.count("\n"), 1)
        self.assertRegex(err, r"^cp: cannot create directory '/tmp/ccc(/a)+': File name too long\n$")
        self.assertEqual(len(err.split("'")[1]), 4096)

    def test_rm_r_of_a_user_takes_a_wide_folder_at_once(self):
        # the rights of the whole tree in one pass, then one step: thousands of files are gone in time
        wide = node("u", T_DIR, 0o40755, [node(f"n{i}", T_FILE, 0o100644, uid=1000) for i in range(20000)],
                    uid=1000)
        self.fs.getfile("/tmp")[7].append(wide)
        self.assertEqual(self.run_in("debian_12", "rm -r /tmp/u", uid=1000), ("", "", 0))
        self.assertFalse(self.exists("/tmp/u"))
        # a node of root in a sticky folder of the user's: the walk, its errors and what it can remove
        wide = node("u", T_DIR, 0o41777, [node("mine", T_FILE, 0o100644, uid=1000), node("root", T_FILE, 0o100644)],
                    uid=0)
        self.fs.getfile("/tmp")[7].append(wide)
        self.assertEqual(self.run_in("debian_12", "rm -rf /tmp/u", uid=1000),
                         ("", "rm: cannot remove '/tmp/u/root': Operation not permitted\n", 1))
        self.assertFalse(self.exists("/tmp/u/mine"))
        self.assertTrue(self.exists("/tmp/u/root"))

    def test_rm_ri_keeps_the_folder_of_a_kept_file(self):
        self.assertEqual(self.run_in("debian_12", "rm -ri d", stdin=b"y\nn\ny\n"),
                         ("", "rm: descend into directory 'd'? rm: remove regular empty file 'd/f'? "
                              "rm: remove directory 'd'? rm: cannot remove 'd': Directory not empty\n", 1))
        self.assertTrue(self.exists("/tmp/w/d/f"))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -ri d", stdin=b"y\nn\ny\n"),
                         ("", "rm: descend into directory 'd'? rm: remove 'd/f'? rm: remove directory 'd'? "
                              "rm: can't remove 'd': Directory not empty\n", 1))
        self.assertTrue(self.exists("/tmp/w/d/f"))
        # GNU leaves the folders above a folder it was not let into without a question (remove.c);
        #   BusyBox asks, and its rmdir says the folder is not empty
        self.fs.getfile("/tmp/w/d")[7].append(node("s", T_DIR, 0o40755, [node("g", T_FILE, 0o100644)]))
        self.assertEqual(self.run_in("debian_12", "rm -ri d", stdin=b"y\ny\nn\n"),
                         ("", "rm: descend into directory 'd'? rm: remove regular empty file 'd/f'? "
                              "rm: descend into directory 'd/s'? ", 0))
        self.assertTrue(self.exists("/tmp/w/d/s/g") and not self.exists("/tmp/w/d/f"))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -ri d", stdin=b"n\n"),
                         ("", "rm: descend into directory 'd'? ", 0))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -ri d", stdin=b"y\nn\ny\n"),
                         ("", "rm: descend into directory 'd'? rm: descend into directory 'd/s'? "
                              "rm: remove directory 'd'? rm: can't remove 'd': Directory not empty\n", 1))
        # an empty folder: GNU asks once, to remove it
        self.assertEqual(self.run_in("debian_12", "rm -ri e", stdin=b"y\n"), ("", "rm: remove directory 'e'? ", 0))
        self.assertFalse(self.exists("/tmp/w/e"))

    def test_links(self):
        T_LINK_MODE = 0o120777
        w = self.fs.getfile("/tmp/w")
        w[7] += [node("ld", T_LINK, T_LINK_MODE, target="d"), node("lf", T_LINK, T_LINK_MODE, target="file")]
        # a slash at the end names the folder the link points to: rmdir of it is no folder to remove
        self.assertEqual(self.run_in("debian_12", "rm ld/"), ("", "rm: cannot remove 'ld/': Is a directory\n", 1))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm ld/"), ("", "rm: 'ld/' is a directory\n", 1))
        self.assertEqual(self.run_in("fw_tplink_c7v5", "rm ld/"), ("", "rm: ld/: is a directory\n", 1))
        self.assertTrue(self.exists("/tmp/w/ld") and self.exists("/tmp/w/d/f"))
        self.assertEqual(self.run_in("debian_12", "rm -r ld/"), ("", "rm: cannot remove 'ld/': Not a directory\n", 1))
        self.assertTrue(self.exists("/tmp/w/ld") and self.exists("/tmp/w/d") and not self.exists("/tmp/w/d/f"))
        self.fs.getfile("/tmp/w/d")[7].append(node("f", T_FILE, 0o100644))
        self.assertEqual(self.run_in("busybox_1.36.1-musl", "rm -rf ld/"),
                         ("", "rm: can't remove 'ld/': Not a directory\n", 1))
        self.assertFalse(self.exists("/tmp/w/d/f"))
        # without the slash the link itself goes
        self.assertEqual(self.run_in("debian_12", "rm -r ld"), ("", "", 0))
        self.assertTrue(self.exists("/tmp/w/d") and not self.exists("/tmp/w/ld"))
        # cp -r, -a, -P and -d copy a link as a link; without them, and with -L, cp copies what it points to
        w[7].append(node("ld", T_LINK, T_LINK_MODE, target="d"))
        for name in ("debian_12", "busybox_1.36.1-musl"):
            for line in ("cp -r ld x1", "cp -a ld x2", "cp -P lf x3", "cp -d lf x4", "cp -rL lf x5", "cp lf x6",
                         "cp -r ld/ x7"):
                with self.subTest(name=name, line=line):
                    self.assertEqual(self.run_in(name, line), ("", "", 0))
                    copy = self.fs.getfile("/tmp/w/" + line.split()[-1], follow_symlinks=False)
                    if line.endswith(("x5", "x6", "x7")):
                        self.assertEqual(copy[1], T_DIR if line.endswith("x7") else T_FILE)
                    else:
                        self.assertEqual((copy[1], copy[8]), (T_LINK, {"ld": "d", "lf": "file"}[line.split()[-2]]))
                    self.run_in(name, "rm -rf " + line.split()[-1])


SHELLS = {}


if __name__ == "__main__":
    unittest.main()


class WideFolderTest(unittest.TestCase):
    """touch of thousands of names in one folder: every name is made, the status is 0 (the real touch does
    that in well under a second; the persona's looked each name up among all the others and ended at its
    time budget with status 1 and nothing said)."""

    def test_touch_makes_thousands_of_names_in_one_folder(self):
        shell = Shell(keys_of("debian_12", REFS["debian_12"]))
        fs = make_fs({"r": [], "bin": []})
        for batch in range(2):
            names = [f"f{i}" for i in range(batch * 4000, (batch + 1) * 4000)]
            start = time.monotonic()
            out, err, status = shell.run(fs, ["touch"] + names, 0, None)
            self.assertEqual((out, err, status), ("", "", 0), batch)
            self.assertLess(time.monotonic() - start, 0.5, batch)
            held = {child[0] for child in fs.getfile("/tmp/w")[fs_module()["A_CONTENTS"]]}
            self.assertTrue(set(names) <= held, batch)
        # the same names again set their time, a link is followed, a link to nothing becomes its file
        out, err, status = shell.run(fs, ["touch", "f1", "f2", "/tmp/w/d/../f3"], 0, None)
        self.assertEqual((out, err, status), ("", "", 0))
        w = fs.getfile("/tmp/w")[fs_module()["A_CONTENTS"]]
        w.append(node("la", T_LINK, 0o120777, target="/tmp/w/a"))
        w.append(node("lg", T_LINK, 0o120777, target="gone"))
        out, err, status = shell.run(fs, ["touch", "-d", "@1700000000", "la", "lg"], 0, None)
        self.assertEqual((out, err, status), ("", "", 0))
        self.assertEqual(fs.getfile("/tmp/w/a")[fs_module()["A_CTIME"]], 1700000000)
        self.assertEqual(fs.getfile("/tmp/w/gone")[fs_module()["A_TYPE"]], T_FILE)
        self.assertEqual(fs.getfile("/tmp/w/lg", follow_symlinks=False)[fs_module()["A_TYPE"]], T_LINK)
        # -h sets the time of the link itself; a name through a file, a slash after a file
        out, err, status = shell.run(fs, ["touch", "-h", "-d", "@1600000000", "la"], 0, None)
        self.assertEqual(status, 0)
        self.assertEqual(fs.getfile("/tmp/w/la", follow_symlinks=False)[fs_module()["A_CTIME"]], 1600000000)
        self.assertEqual(fs.getfile("/tmp/w/a")[fs_module()["A_CTIME"]], 1700000000)
        out, err, status = shell.run(fs, ["touch", "file/x", "a/", "nosuch/y", "d/"], 0, None)
        self.assertEqual(err, "touch: cannot touch 'file/x': Not a directory\n"
                              "touch: cannot touch 'a/': Not a directory\n"
                              "touch: cannot touch 'nosuch/y': No such file or directory\n")
        self.assertEqual(status, 1)


class TerminalQuestionTest(unittest.TestCase):
    """-i and -I of rm, cp and mv at the terminal: the prompt is written and the command waits for a line
    (lineReceived), ctrl-d answers no; a pipe answers at once (the recorded cases), an exec channel's bytes
    are the answers at the end of its input."""

    def setUp(self):
        self.shell = Shell(keys_of("debian_12", REFS["debian_12"]))
        self.busybox = Shell(keys_of("busybox_1.36.1-musl", REFS["busybox_1.36.1-musl"]))

    def start(self, shell, fs, words, stdin=None, protocol=None):
        cmd = shell.namespace["commands"][words[0]](words[1:], stdin, fs)
        cmd.cwd, cmd.user = "/tmp/w", {"uid": 0, "gid": 0}
        if protocol is not None:
            cmd.protocol = protocol
        Log.errors.clear()
        cmd.start()
        self.assertEqual(Log.errors, [])
        return cmd

    def exists(self, fs, path):
        return fs.getfile(path, follow_symlinks=False) is not None

    def test_rm_i_waits_for_the_answer(self):
        for answer, kept in (("y", False), ("yes", False), ("Y", False), ("n", True), ("", True), ("maybe", True)):
            fs = make_fs({"r": [], "bin": []})
            cmd = self.start(self.shell, fs, ["rm", "-i", "file"])
            self.assertFalse(cmd.exited)
            self.assertEqual(cmd.err, "rm: remove regular empty file 'file'? ")
            cmd.lineReceived(answer)
            self.assertTrue(cmd.exited, answer)
            self.assertEqual((cmd.exit_code, cmd.err), (0, "rm: remove regular empty file 'file'? "), answer)
            self.assertEqual(self.exists(fs, "/tmp/w/file"), kept, answer)

    def test_ctrl_d_answers_no_to_every_question(self):
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "-i", "a", "b"])
        self.assertEqual(cmd.err, "rm: remove regular empty file 'a'? ")
        cmd.eofReceived()
        self.assertTrue(cmd.exited)
        self.assertEqual(cmd.err, "rm: remove regular empty file 'a'? rm: remove regular empty file 'b'? ")
        self.assertEqual(cmd.exit_code, 0)
        self.assertTrue(self.exists(fs, "/tmp/w/a") and self.exists(fs, "/tmp/w/b"))

    def test_rm_ri_asks_down_the_tree(self):
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "-ri", "d"])
        self.assertEqual(cmd.err, "rm: descend into directory 'd'? ")
        cmd.lineReceived("y")
        self.assertFalse(cmd.exited)
        self.assertEqual(cmd.err, "rm: descend into directory 'd'? rm: remove regular empty file 'd/f'? ")
        cmd.lineReceived("n")
        # the folder is asked for although a node in it stays, and then cannot go (recorded with 9.1)
        self.assertEqual((cmd.exited, cmd.err.rsplit("? ", 2)[-2]), (False, "rm: remove directory 'd'"))
        cmd.lineReceived("y")
        self.assertEqual((cmd.exited, cmd.exit_code), (True, 1))
        self.assertTrue(cmd.err.endswith("rm: remove directory 'd'? rm: cannot remove 'd': Directory not empty\n"))
        self.assertTrue(self.exists(fs, "/tmp/w/d/f"))
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "-ri", "d"])
        cmd.lineReceived("y")
        cmd.lineReceived("y")
        self.assertFalse(cmd.exited)
        self.assertTrue(cmd.err.endswith("rm: remove directory 'd'? "))
        cmd.lineReceived("y")
        self.assertEqual((cmd.exited, cmd.exit_code, self.exists(fs, "/tmp/w/d")), (True, 0, False))

    def test_rm_I_asks_once(self):
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "-I", "-r", "d", "e"])
        self.assertEqual(cmd.err, "rm: remove 2 arguments recursively? ")
        cmd.lineReceived("y")
        self.assertEqual((cmd.exited, cmd.exit_code), (True, 0))
        self.assertFalse(self.exists(fs, "/tmp/w/d") or self.exists(fs, "/tmp/w/e"))

    def test_cp_and_mv_ask_before_they_overwrite(self):
        for words, prompt in ((["cp", "-i", "a", "b"], "cp: overwrite 'b'? "), (["mv", "-i", "a", "b"], "mv: overwrite 'b'? ")):
            fs = make_fs({"r": [], "bin": []})
            cmd = self.start(self.shell, fs, words)
            self.assertEqual((cmd.exited, cmd.err), (False, prompt), words)
            cmd.lineReceived("n")
            self.assertEqual((cmd.exited, cmd.exit_code, self.exists(fs, "/tmp/w/a")), (True, 0, True), words)
            fs = make_fs({"r": [], "bin": []})
            cmd = self.start(self.shell, fs, words)
            cmd.lineReceived("y")
            self.assertEqual((cmd.exited, cmd.exit_code), (True, 0), words)
            self.assertEqual(self.exists(fs, "/tmp/w/a"), words[0] == "cp", words)
        # the output before a question is written before it, and what follows after the answer
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "-vi", "a", "b"])
        self.assertEqual((cmd.out, cmd.err), (b"", "rm: remove regular empty file 'a'? "))
        cmd.lineReceived("y")
        self.assertEqual((cmd.out, cmd.err), (b"removed 'a'\n", "rm: remove regular empty file 'a'? "
                                                                "rm: remove regular empty file 'b'? "))
        cmd.lineReceived("n")
        self.assertEqual((cmd.exited, cmd.exit_code, cmd.out), (True, 0, b"removed 'a'\n"))

    def test_busybox_asks_the_same_way(self):
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.busybox, fs, ["rm", "-ri", "d"])
        self.assertEqual(cmd.err, "rm: descend into directory 'd'? ")
        cmd.lineReceived("y")
        self.assertEqual(cmd.err, "rm: descend into directory 'd'? rm: remove 'd/f'? ")
        cmd.lineReceived("y")
        self.assertTrue(cmd.err.endswith("rm: remove directory 'd'? "))
        cmd.lineReceived("y")
        self.assertEqual((cmd.exited, cmd.exit_code, self.exists(fs, "/tmp/w/d")), (True, 0, False))

    def test_a_pipe_answers_at_once_and_an_exec_channel_at_its_end(self):
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "-i", "a", "b"], stdin=b"y\n")
        self.assertEqual((cmd.exited, cmd.exit_code), (True, 0))
        self.assertEqual((self.exists(fs, "/tmp/w/a"), self.exists(fs, "/tmp/w/b")), (False, True))

        class Exec:
            execcmd = "rm -i a b"
            input_data = b""

            class events:
                @staticmethod
                def dispatch(*args, **kwargs):
                    pass

        fs = make_fs({"r": [], "bin": []})
        protocol = Exec()
        cmd = self.start(self.shell, fs, ["rm", "-i", "a", "b"], protocol=protocol)
        self.assertEqual((cmd.exited, cmd.err), (False, "rm: remove regular empty file 'a'? "))
        protocol.input_data = b"n\ny\n"
        cmd.eofReceived()
        self.assertEqual((cmd.exited, cmd.exit_code), (True, 0))
        self.assertEqual((self.exists(fs, "/tmp/w/a"), self.exists(fs, "/tmp/w/b")), (True, False))

    def test_a_line_outside_a_question_goes_on_as_before(self):
        fs = make_fs({"r": [], "bin": []})
        cmd = self.start(self.shell, fs, ["rm", "a"])
        self.assertTrue(cmd.exited)
        cmd.lineReceived("x")  # the dispatch of the input, nothing else
        cmd.eofReceived()

