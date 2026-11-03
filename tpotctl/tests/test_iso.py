"""installer/iso: the T-Pot ISO (a preview). Its preseed answers only what is safe on any machine,
late.sh puts the T-Pot installer into the new system, the login hook (profile.sh) and start.sh hand
over to the install.sh of the ISO, isotree.py patches the boot menus of the Debian netinst and
build.sh checks the netinst and the ISO it writes, writes USB sticks (only those it may), the
checksums, signatures and upload of a release, keeps its netinst cache in step with Debian and
shows its menu at a terminal.

Nothing here downloads, calls Docker, writes a disk or the checkout: the scripts run on temporary
folders with stubs (lsblk, findmnt, diskutil, df, dd, gpg, gh, curl), a stick is a file,
isotree.py runs on fixtures modelled on the files of the Debian 13 netinst.
"""

import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib

from tpotctl.tests import ptyhelper
from tpotctl.tests import test_scripts as base

REPO = base.REPO
ISO_DIR = os.path.join(REPO, "installer", "iso")
BUILD = os.path.join(ISO_DIR, "build.sh")
SH = shutil.which("sh") or "/bin/sh"
BASH = shutil.which("bash") or "/bin/bash"

_spec = importlib.util.spec_from_file_location("isotree", os.path.join(ISO_DIR, "isotree.py"))
isotree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(isotree)

# --- fixtures: the files of the Debian 13 netinst the patches are written for, shortened

GRUB_AMD64 = """if loadfont $font ; then
  set gfxmode=800x600
  terminal_output gfxterm
fi

insmod play
play 960 440 1 0 4 440 1
set theme=/boot/grub/theme/1
menuentry --hotkey=g 'Graphical install' {
    set background_color=black
    linux    /install.amd/vmlinuz vga=788 --- quiet\x20
    initrd   /install.amd/gtk/initrd.gz
}
menuentry --hotkey=i 'Install' {
    set background_color=black
    linux    /install.amd/vmlinuz vga=788 --- quiet\x20
    initrd   /install.amd/initrd.gz
}
submenu --hotkey=a 'Advanced options ...' {
    set theme=/boot/grub/theme/1-1
    menuentry --hotkey=a '... Automated install' {
        set background_color=black
        linux    /install.amd/vmlinuz auto=true priority=critical vga=788 --- quiet\x20
        initrd   /install.amd/initrd.gz
    }
}
"""

GRUB_ARM64 = """set menu_color_normal=cyan/blue
set menu_color_highlight=white/blue

insmod gzio

menuentry 'Install' {
    set background_color=black
    linux    /install.a64/vmlinuz  --- quiet
    initrd   /install.a64/initrd.gz
}
menuentry 'Graphical install' {
    set background_color=black
    linux    /install.a64/vmlinuz  --- quiet
    initrd   /install.a64/gtk/initrd.gz
}
submenu --hotkey=a 'Advanced options ...' {
    menuentry '... Expert install' {
        set background_color=black
        linux    /install.a64/vmlinuz  priority=low ---
        initrd   /install.a64/initrd.gz
    }
}
"""

MENU_CFG = """menu hshift 4
menu width 70

menu title \x07Debian GNU/Linux installer menu (BIOS mode)
include stdmenu.cfg
include gtk.cfg
include txt.cfg
menu begin advanced
    menu label ^Advanced options
\tinclude adtxt.cfg
menu end
include x86menu.cfg
include spkgtk.cfg
include spk.cfg
"""

TXT_CFG = "label install\n\tmenu label ^Install\n\tkernel /install.amd/vmlinuz\n" \
          "\tappend vga=788 initrd=/install.amd/initrd.gz --- quiet \n"
GTK_CFG = "default installgui\nlabel installgui\n\tmenu label ^Graphical install\n\tmenu default\n" \
          "\tkernel /install.amd/vmlinuz\n\tappend vga=788 initrd=/install.amd/gtk/initrd.gz --- quiet \n"
SPKGTK_CFG = "label installspk\n\tmenu label Install with ^speech synthesis\n\tkernel /install.amd/vmlinuz\n" \
             "\tappend vga=788 initrd=/install.amd/gtk/initrd.gz speakup.synth=soft --- quiet \n" \
             "# timeout to speech-enabled install\ntimeout 300\n" \
             "ontimeout /install.amd/vmlinuz vga=788 initrd=/install.amd/gtk/initrd.gz speakup.synth=soft --- quiet \n" \
             "menu autoboot Press a key, otherwise speech synthesis will be started in # second{,s}...\n"
STDMENU_CFG = """menu background splash.png
menu color title\t* #FFFFFFFF *
menu color sel\t\t* #ffffffff #76a1d0ff *
menu color hotsel\t1;7;37;40 #ffffffff #76a1d0ff *
menu vshift 8
menu rows 12
"""

THEME = """title-color: "white"
title-text: "Debian GNU/Linux 13.7.0"
desktop-color: "black"
desktop-image: "/isolinux/splash.png"

+ vbox {
  top = 80
  left = 45%
  width = 60%
  + hbox {
    left = 0
    + label {text = "Debian GNU/Linux UEFI Installer menu" font = "Sans 10" color = "#c0c0c0" align = "left"}
  }
}

+ boot_menu {
  item_color = #c0c0c0
  selected_item_color = "black"
  selected_item_pixmap_style = "hl_*.png"
}
"""
DARK_THEME = THEME.replace('desktop-image: "/isolinux/splash.png"\n', "")

# xorriso -report_el_torito plain -report_system_area plain of the amd64 netinst, shortened
REPORT = """El Torito catalog  : 1028  1
El Torito cat path : /isolinux/boot.cat
El Torito images   :   N  Pltf  B   Emul  Ld_seg  Hdpt  Ldsiz         LBA
El Torito boot img :   1  BIOS  y   none  0x0000  0x00      4        2869
El Torito boot img :   2  UEFI  y   none  0x0000  0x00   7360        1029
El Torito img path :   1  /isolinux/isolinux.bin
El Torito img opts :   1  boot-info-table isohybrid-suitable
El Torito img path :   2  /boot/grub/efi.img
System area options: 0x00000102
System area summary: MBR isohybrid cyl-align-on GPT APM
ISO image size/512 : 1548288
MBR partition table:   N Status  Type        Start       Blocks
MBR partition      :   1   0x80  0x00            0      1548288
MBR partition      :   2   0x00  0xef         4116         7360
MBR partition path :   2  /boot/grub/efi.img
GPT disk GUID      :      2a59dbf8b70f6c44adc143937fce8190
GPT partition name :   1  490053004f00480079006200720069006400
GPT type GUID      :   1  a2a0d0ebe5b9334487c068b6b72699c7
GPT start and size :   2  4116  7360
GPT partition path :   2  /boot/grub/efi.img
APM partition name :   1  EFI
APM partition type :   1  Apple_HFS
APM start and size :   1  1029  1840
APM partition path :   1  /boot/grub/efi.img
"""
# the same ISO written again: the blocks and the GUIDs moved, the boot records are the same
REPORT_MOVED = (REPORT.replace("1028  1", "571  1").replace("2869", "2412").replace("        1029", "         572")
                .replace("1548288", "1546240").replace("4116", "2288").replace("2a59dbf8b70f6c44adc1", "99b3df24a8569046"))


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(text, bytes) else "w") as handle:
        handle.write(text)
    os.chmod(path, mode)


def netinst_tree(folder, arch):
    """The boot files of a netinst as build.sh extracts them."""
    write(os.path.join(folder, "boot/grub/grub.cfg"), GRUB_AMD64 if arch == "amd64" else GRUB_ARM64)
    if arch == "arm64":
        write(os.path.join(folder, "boot/grub/font.pf2"), b"FILE\0\0\0\4PFF2")
    if arch == "amd64":
        for name, text in (("menu.cfg", MENU_CFG), ("txt.cfg", TXT_CFG), ("gtk.cfg", GTK_CFG),
                           ("stdmenu.cfg", STDMENU_CFG), ("spkgtk.cfg", SPKGTK_CFG)):
            write(os.path.join(folder, "isolinux", name), text)
        for name in ("1", "1-1"):
            write(os.path.join(folder, "boot/grub/theme", name), THEME)
        write(os.path.join(folder, "boot/grub/theme/dark-1"), DARK_THEME)
        write(os.path.join(folder, "boot/grub/theme/hl_c.png"), isotree.solid_png(20, 20, (0x76, 0xA1, 0xD0)))


def png_pixels(data):
    """(width, height, rows of RGB bytes) of an 8-bit RGB PNG without filters (the ones isotree writes),
    every chunk CRC checked."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        crc, = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, kind
        chunks[kind] = chunks.get(kind, b"") + body
        pos += 12 + length
    width, height, depth, colour, _c, _f, _i = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    assert (depth, colour) == (8, 2)
    raw = zlib.decompress(chunks[b"IDAT"])
    stride = width * 3 + 1
    assert all(raw[y * stride] == 0 for y in range(height))
    return width, height, [raw[y * stride + 1:(y + 1) * stride] for y in range(height)]


def function(name):
    """The bash function name of build.sh alone (build.sh runs its main part when sourced)."""
    match = re.search(rf"^{name} \(\) \{{\n.*?^\}}\n", base.read("installer/iso/build.sh"), re.M | re.S)
    assert match, name
    return match.group(0)


class PreseedTest(unittest.TestCase):
    """preseed.cfg answers only what is safe on any machine: no disk, no boot loader, no account, no
    password, no language or keyboard; the host name and the proxy are still asked."""

    def setUp(self):
        self.answers = {}
        for line in base.read("installer/iso/preseed.cfg").split("\n"):
            if not line.strip() or line.startswith("#"):
                continue
            match = re.match(r"^(d-i|tasksel|popularity-contest) (\S+) (string|boolean|multiselect|select|seen)"
                             r"(?: (.*))?$", line)
            self.assertIsNotNone(match, line)
            self.answers[(match.group(2), match.group(3))] = match.group(4) or ""

    def test_nothing_unsafe_is_answered(self):
        unsafe = ("partman", "grub-installer/", "passwd/user", "passwd/root-password", "passwd/username",
                  "user-setup/", "debian-installer/", "keyboard-configuration/", "console-setup/",
                  "localechooser/", "time/zone", "netcfg/choose_interface", "debconf/priority",
                  "auto-install/", "preseed/url", "preseed/include",
                  "finish-install/", "cdrom-detect/")
        for question, _kind in self.answers:
            self.assertFalse(question.startswith(unsafe), question)
        answered = [line for line in base.read("installer/iso/preseed.cfg").split("\n") if not line.startswith("#")]
        self.assertNotIn("password", "\n".join(answered).lower())

    def test_no_root_login_the_user_gets_sudo(self):
        self.assertEqual(self.answers[("passwd/root-login", "boolean")], "false")

    def test_the_host_name_and_the_proxy_are_still_asked(self):
        self.assertEqual(self.answers[("netcfg/get_hostname", "string")], "t-pot")
        self.assertEqual(self.answers[("netcfg/get_hostname", "seen")], "false")
        self.assertEqual(self.answers[("netcfg/get_domain", "seen")], "false")
        self.assertEqual(self.answers[("mirror/http/proxy", "seen")], "false")

    def test_a_server_with_ssh_and_no_desktop(self):
        tasks = [task.strip() for task in self.answers[("tasksel/first", "multiselect")].split(",")]
        self.assertEqual(tasks, ["standard", "ssh-server"])
        packages = self.answers[("pkgsel/include", "string")].split()
        self.assertEqual(packages, sorted(set(packages)))
        self.assertLessEqual({"ca-certificates", "console-setup", "curl", "fonts-dejavu-core", "keyboard-configuration",
                              "matchbox-window-manager", "sudo", "xauth", "xinit", "xserver-xorg-core",
                              "xserver-xorg-input-libinput", "xserver-xorg-legacy", "xserver-xorg-video-fbdev",
                              "xterm"}, set(packages))
        # the T-Pot installer finds its packages there and installs nothing before its questions
        installer = re.search(r'^myPACKAGES_DEBIAN="([^"]+)"$', base.read("install.sh"), re.M).group(1).split()
        self.assertLessEqual(set(installer), set(packages))
        self.assertFalse({"task-desktop", "gdm3", "lightdm", "sddm"} & set(packages))

    def test_the_early_command_repairs_keyboard_detection_only(self):
        self.assertEqual(self.answers[("preseed/early_command", "string")], "sh /cdrom/tpot/early.sh")

    def test_the_late_command_runs_late_sh_of_the_iso(self):
        self.assertEqual(self.answers[("preseed/late_command", "string")], "sh /cdrom/tpot/late.sh")
        self.assertEqual(isotree.PRESEED, "preseed/file=/cdrom/tpot/preseed.cfg")


class EarlyTest(unittest.TestCase):
    FILES = ("usr/share/console-setup/keyboard-configuration.config",
             "var/lib/dpkg/info/console-setup-udeb.postinst",
             "usr/lib/base-installer.d/20console-setup")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.root = os.path.join(self.tmp, "root")
        self.trace = os.path.join(self.tmp, "trace")
        self.before = "#!/bin/sh\nkeyboard_present () {\nreturn 1\n}\nkeyboard_present\n"
        for file in self.FILES:
            write(os.path.join(self.root, file), self.before)
        write(os.path.join(self.root, "usr/share/debconf/confmodule"),
              'db_fget () { test "$*" = "keyboard-configuration/xkb-keymap seen"; RET="$SEEN"; }\n'
              'db_settitle () { echo "title $*" >>"$TRACE"; }\n')
        write(os.path.join(self.bin, "udevadm"),
              '#!/bin/sh\nprintf "%s\\n" "$INPUT"\nexit "${UDEV_RC:-0}"\n', 0o755)
        for command in ("logger", "udpkg"):
            write(os.path.join(self.bin, command),
                  f'#!/bin/sh\necho "{command} $*" >>"$TRACE"\nexit "${{{command.upper()}_RC:-0}}"\n',
                  0o755)

    def run_early(self, **env):
        environ = {**os.environ, "PATH": self.bin + os.pathsep + os.environ["PATH"],
                   "INPUT": "E: ID_INPUT_KEYBOARD=1", "SEEN": "false", "TRACE": self.trace, **env}
        return subprocess.run([SH, os.path.join(ISO_DIR, "early.sh"), self.root],
                              env=environ, capture_output=True, text=True, timeout=30)

    def test_missed_keyboard_is_asked_and_all_target_hooks_are_repaired(self):
        result = self.run_early()
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.trace) as handle:
            trace = handle.read()
        self.assertIn("title debian-installer/console-setup-udeb/title", trace)
        self.assertIn("udpkg --force-configure --configure console-setup-udeb", trace)
        for file in self.FILES:
            env = {**os.environ, "PATH": self.bin + os.pathsep + os.environ["PATH"],
                   "INPUT": "E: ID_INPUT_KEYBOARD=1"}
            detected = subprocess.run([SH, os.path.join(self.root, file)], env=env, timeout=30)
            self.assertEqual(detected.returncode, 0, file)
            not_keyboard = subprocess.run([SH, os.path.join(self.root, file)],
                                          env={**env, "INPUT": "E: ID_INPUT_KEY=1"}, timeout=30)
            self.assertEqual(not_keyboard.returncode, 1, file)
        again = self.run_early(SEEN="true")
        self.assertEqual(again.returncode, 0, again.stderr)
        for file in self.FILES:
            with open(os.path.join(self.root, file)) as handle:
                self.assertEqual(handle.read().count("udevadm info --export-db"), 1)

    def test_an_answered_layout_is_not_asked_again(self):
        result = self.run_early(SEEN="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.trace))

    def test_headless_mouse_and_power_button_are_not_keyboards(self):
        for devices in ("", "E: ID_INPUT_MOUSE=1", "E: ID_INPUT_KEY=1"):
            result = self.run_early(INPUT=devices)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.exists(self.trace))
            for file in self.FILES:
                with open(os.path.join(self.root, file)) as handle:
                    self.assertEqual(handle.read(), self.before)

    def test_inspection_or_configuration_errors_are_not_hidden(self):
        result = self.run_early(UDEV_RC="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot inspect the input devices", result.stderr)
        self.assertFalse(os.path.exists(self.trace))
        result = self.run_early(UDPKG_RC="1")
        self.assertNotEqual(result.returncode, 0)
        with open(self.trace) as handle:
            self.assertIn("udpkg --force-configure --configure console-setup-udeb", handle.read())

    def test_changed_debian_scripts_stop_before_partial_patching(self):
        write(os.path.join(self.root, self.FILES[-1]), "changed\n")
        result = self.run_early()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unrecognized Debian keyboard detection", result.stderr)
        self.assertFalse(os.path.exists(self.trace))
        with open(os.path.join(self.root, self.FILES[0])) as handle:
            self.assertEqual(handle.read(), self.before)


class LateTest(unittest.TestCase):
    """late.sh (busybox sh in the Debian installer): the T-Pot installer of the ISO into the new system."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = os.path.join(self.tmp, "cdrom", "tpot")
        self.target = os.path.join(self.tmp, "target")
        os.makedirs(self.target)
        self.bin = os.path.join(self.tmp, "bin")
        self.calls = os.path.join(self.tmp, "calls")
        stub(self.bin, "debconf-get", 'echo "debconf-get $*" >> "$CALLS"\n[ "$1" != passwd/username ] || echo tester\n')
        stub(self.bin, "in-target", 'echo "in-target $*" >> "$CALLS"\nexit "${IN_TARGET_RC:-0}"\n')
        # read-only, like the files in the ISO
        for name in ("install.sh", "start.sh", "terminal.sh", "prepare.sh", "profile.sh"):
            write(os.path.join(self.src, name), f"# {name}\n", 0o555)
        for name in ("iso.conf", "version", "tpot.issue", "preseed.cfg", "tpotce.tar", "gum_2.0.2_Linux_x86_64.tar.gz",
                     "wheels/textual-1.0-py3-none-any.whl", "wheels/rich-1.0-py3-none-any.whl"):
            write(os.path.join(self.src, name), f"# {name}\n", 0o444)

    def run_late(self, **env):
        environ = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": self.calls, **env}
        return subprocess.run([SH, os.path.join(ISO_DIR, "late.sh"), self.src, self.target],
                              capture_output=True, text=True, timeout=30, env=environ)

    def test_the_installer_the_hook_and_the_hint_go_into_the_new_system(self):
        result = self.run_late()
        self.assertEqual(result.returncode, 0, result.stderr)
        share = "usr/local/share/tpot-iso/"
        expected = {share + "install.sh": 0o755, share + "start.sh": 0o755, share + "terminal.sh": 0o755,
                    share + "prepare.sh": 0o755, "usr/local/bin/tpot-terminal": 0o755,
                    share + "iso.conf": 0o644, share + "version": 0o644, share + "tpotce.tar": 0o644,
                    share + "gum_2.0.2_Linux_x86_64.tar.gz": 0o644, share + "wheels/textual-1.0-py3-none-any.whl": 0o644,
                    share + "wheels/rich-1.0-py3-none-any.whl": 0o644,
                    "etc/profile.d/tpot-iso.sh": 0o644, "etc/issue.d/tpot.issue": 0o644}
        for rel, mode in expected.items():
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.target, rel)).st_mode), mode, rel)
        with open(os.path.join(self.target, "etc/profile.d/tpot-iso.sh"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# profile.sh\n")
        self.assertEqual(os.readlink(os.path.join(self.target, "usr/local/bin/tpot-terminal")),
                         "../share/tpot-iso/terminal.sh")
        found = sorted(os.path.relpath(os.path.join(root, name), self.target)
                       for root, _dirs, names in os.walk(self.target) for name in names)
        self.assertEqual(found, sorted(expected))
        # T-Pot for the user of the Debian installer, in the new system
        self.assertEqual(base.read(self.calls).splitlines(),
                         ["debconf-get passwd/username", "in-target bash /usr/local/share/tpot-iso/prepare.sh tester"])
        self.assertIn("T-Pot is ready in the home folder of tester", result.stdout)

    def test_without_gum_and_wheels_and_when_prepare_sh_fails_debian_goes_on(self):
        shutil.rmtree(os.path.join(self.src, "wheels"))
        os.remove(os.path.join(self.src, "gum_2.0.2_Linux_x86_64.tar.gz"))
        result = self.run_late(IN_TARGET_RC="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("T-Pot could not be prepared (/var/log/tpot-iso.log)", result.stderr)
        share = os.path.join(self.target, "usr/local/share/tpot-iso")
        self.assertFalse(os.path.exists(os.path.join(share, "wheels")))
        self.assertTrue(os.path.isfile(os.path.join(share, "tpotce.tar")))

    def test_a_missing_file_stops_before_anything_is_written(self):
        os.chmod(self.src, 0o755)
        os.remove(os.path.join(self.src, "iso.conf"))
        result = self.run_late()
        self.assertEqual(result.returncode, 1)
        self.assertIn("iso.conf is missing", result.stderr)
        self.assertEqual(os.listdir(self.target), [])


class PrepareTest(unittest.TestCase):
    """prepare.sh (late.sh runs it in the new system, as root): the T-Pot of the ISO into the home
    folder of the user (its .git, the commit of iso.conf), gum when its sha256 is the one of ui.sh of
    that T-Pot, tpot setup with the wheels of the ISO as that user in a plain environment, the sshd
    line of the playbook unless sshd rejects it; it never stops the Debian installer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, "home", "tester")
        self.share = os.path.join(self.tmp, "share")
        self.sshd = os.path.join(self.tmp, "sshd_config.d")
        self.log = os.path.join(self.tmp, "tpot-iso.log")
        self.bin = os.path.join(self.tmp, "bin")
        self.calls = os.path.join(self.tmp, "calls")
        for folder in (self.home, self.share, self.sshd, os.path.join(self.share, "wheels")):
            os.makedirs(folder)
        write(os.path.join(self.share, "wheels", "rich-1.0-py3-none-any.whl"), "wheel")
        stub(self.bin, "runuser", 'echo "runuser $1 $2" >> "$CALLS"\n'
                                  'while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n')
        stub(self.bin, "getent", f'case "$1 $2" in\n'
                                 f'  "group sudo") echo "sudo:x:27:${{SUDO_MEMBERS-tester,other}}" ;;\n'
                                 f'  "passwd tester") echo "tester:x:1000:1000::{self.home}:/bin/bash" ;;\n'
                                 f'  "passwd root") echo "root:x:0:0::/root:/bin/bash" ;;\n'
                                 f'  *) exit 2 ;;\nesac\n')
        stub(self.bin, "sshd", 'echo "sshd $* $(cat "$SSHD_DIR/tpot.conf" 2>/dev/null)" >> "$CALLS"\n'
                               'case "${SSHD_RULE:-ok}" in\n  ok) exit 0 ;;\n  broken) exit 1 ;;\n'
                               '  line) [ ! -e "$SSHD_DIR/tpot.conf" ] ;;\nesac\n')
        stub(self.bin, "uname", 'echo "${MACHINE:-x86_64}"\n')
        # gum of the ISO and the T-Pot whose ui.sh pins it
        self.gum = os.path.join(self.share, "gum_2.0.2_Linux_x86_64.tar.gz")
        with tarfile.open(self.gum, "w:gz") as tar:
            body = b"#!/bin/sh\necho gum\n"
            info = tarfile.TarInfo("gum_2.0.2_Linux_x86_64/gum")
            info.size, info.mode = len(body), 0o755
            tar.addfile(info, io.BytesIO(body))
            info = tarfile.TarInfo("gum_2.0.2_Linux_x86_64/README.md")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"gum\n"))
        with open(self.gum, "rb") as handle:
            sha = hashlib.sha256(handle.read())
        repo = os.path.join(self.tmp, "repo")
        write(os.path.join(repo, "installer/lib/ui.sh"),
              f'myUI_GUM_VERSION="2.0.2"\nmyUI_GUM_SHA256_x86_64="{sha.hexdigest()}"\n'
              f'myUI_GUM_SHA256_arm64="{"0" * 64}"\n')
        write(os.path.join(repo, "tpot"),
              '#!/bin/sh\n{ echo "args $*"; echo "home $HOME"; echo "user $USER"; echo "gum ${TPOT_GUM-}";\n'
              '  echo "calls ${CALLS:-unset}"; } > "$HOME/tpot-setup.txt"\n[ ! -e "$HOME/setup-fails" ]\n', 0o755)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=master"]
        subprocess.run(git + ["init", "-q", repo], check=True)
        subprocess.run(git + ["-C", repo, "add", "-A"], check=True)
        subprocess.run(git + ["-C", repo, "commit", "-qm", "T-Pot"], check=True)
        self.commit = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                                     check=True).stdout.strip()
        with tarfile.open(os.path.join(self.share, "tpotce.tar"), "w") as tar:
            tar.add(os.path.join(repo, ".git"), arcname=".git")
        self.conf(self.commit)
        self.payload = {}
        for name in ("tpotce.tar", os.path.basename(self.gum)):
            with open(os.path.join(self.share, name), "rb") as handle:
                self.payload[name] = handle.read()

    def again(self):
        """The share and the home folder as before the first run."""
        for name, data in self.payload.items():
            write(os.path.join(self.share, name), data)
        for name in ("tpotce", ".local", "tpot-setup.txt"):
            path = os.path.join(self.home, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)

    def conf(self, commit):
        write(os.path.join(self.share, "iso.conf"),
              "# The T-Pot ISO\nTPOT_REPO_URL=https://github.com/telekom-security/tpotce\n"
              f"TPOT_BRANCH=master\nTPOT_COMMIT={commit}\n")

    def run_prepare(self, *args, **env):
        environ = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": self.calls,
                   "SSHD_DIR": self.sshd, "TPOT_ISO_SHARE": self.share, "TPOT_ISO_LOG": self.log,
                   "TPOT_ISO_SSHD": self.sshd, **env}
        result = subprocess.run([BASH, os.path.join(ISO_DIR, "prepare.sh"), *args], capture_output=True,
                                text=True, timeout=60, env=environ)
        self.assertEqual(result.stdout + result.stderr, "", "everything goes into the log")
        return result.returncode, base.read(self.log)

    def setup_call(self):
        path = os.path.join(self.home, "tpot-setup.txt")
        return base.read(path).splitlines() if os.path.exists(path) else None

    def test_tpot_gum_and_the_manager_go_to_the_user(self):
        rc, log = self.run_prepare("tester")
        self.assertEqual(rc, 0, log)
        tpotce = os.path.join(self.home, "tpotce")
        head = subprocess.run(["git", "-C", tpotce, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual(head.strip(), self.commit)
        status = subprocess.run(["git", "-C", tpotce, "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "", "a checkout as git clone leaves it")
        gum = os.path.join(self.home, ".local/share/tpotce/bin/gum")
        self.assertEqual(base.read(gum), "#!/bin/sh\necho gum\n")
        self.assertEqual(stat.S_IMODE(os.stat(gum).st_mode), 0o755)
        self.assertEqual(sorted(os.listdir(os.path.dirname(gum))), ["gum"])
        # tpot setup as the user, in an environment of its own
        self.assertEqual(self.setup_call(), [f"args setup --wheels {self.share}/wheels", f"home {self.home}",
                                             "user tester", "gum off", "calls unset"])
        self.assertIn("runuser -u tester", base.read(self.calls))
        # the line of the playbook for the colours of an SSH login
        drop_in = os.path.join(self.sshd, "tpot.conf")
        self.assertEqual(base.read(drop_in), "AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION\n")
        self.assertEqual(stat.S_IMODE(os.stat(drop_in).st_mode), 0o644)
        # T-Pot and gum are not kept twice, the wheels stay for tpot setup of another branch
        self.assertEqual(sorted(os.listdir(self.share)), ["iso.conf", "wheels"])
        self.assertIn("T-Pot ISO: gum 2.0.2 is in", log)
        self.assertIn("T-Pot ISO: the T-Pot Manager is set up.", log)

    def test_without_an_argument_the_first_member_of_sudo(self):
        rc, log = self.run_prepare()
        self.assertEqual(rc, 0, log)
        self.assertTrue(os.path.isdir(os.path.join(self.home, "tpotce", ".git")))
        self.again()
        rc, log = self.run_prepare(SUDO_MEMBERS="")
        self.assertEqual(rc, 1)
        self.assertIn("no user with a home folder (none)", log)

    def test_root_or_a_user_without_a_home_gets_nothing(self):
        for user in ("root", "nobody"):
            with self.subTest(user=user):
                rc, log = self.run_prepare(user)
                self.assertEqual(rc, 1)
                self.assertIn(f"no user with a home folder ({user})", log)
                self.assertFalse(os.path.exists(os.path.join(self.home, "tpotce")))
                self.assertFalse(os.path.exists(self.calls) and "runuser" in base.read(self.calls))
                self.assertTrue(os.path.isfile(os.path.join(self.share, "tpotce.tar")))

    def test_a_tpotce_of_another_commit_is_not_left_behind(self):
        self.conf("0" * 40)
        rc, log = self.run_prepare("tester")
        self.assertEqual(rc, 1)
        self.assertIn("could not be made from tpotce.tar", log)
        self.assertFalse(os.path.exists(os.path.join(self.home, "tpotce")))
        self.assertIsNone(self.setup_call())
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))

    def test_an_existing_tpotce_stays_as_it_is(self):
        write(os.path.join(self.home, "tpotce", "mine"), "mine")
        rc, log = self.run_prepare("tester")
        self.assertEqual(rc, 0, log)
        self.assertEqual(os.listdir(os.path.join(self.home, "tpotce")), ["mine"])
        self.assertIn("tpotce exists, it stays as it is", log)

    def test_a_gum_that_is_not_the_one_of_ui_sh_stays_out(self):
        with open(self.gum, "ab") as handle:
            handle.write(b"\0")
        rc, log = self.run_prepare("tester")
        self.assertEqual(rc, 0, log)
        self.assertIn("is not the gum of ui.sh (sha256), left out", log)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local/share/tpotce/bin")))
        self.assertEqual(self.setup_call()[0], f"args setup --wheels {self.share}/wheels")

    def test_another_machine_gets_its_own_gum_or_none(self):
        for machine, text in (("aarch64", "the ISO carries no gum 2.0.2 for arm64"), ("riscv64", "no gum for riscv64")):
            with self.subTest(machine=machine):
                rc, log = self.run_prepare("tester", MACHINE=machine)
                self.assertEqual(rc, 0, log)
                self.assertIn(text, log)
                self.assertFalse(os.path.exists(os.path.join(self.home, ".local/share/tpotce/bin/gum")))
                self.again()

    def test_a_failed_setup_or_no_wheels_leave_it_to_the_first_login(self):
        write(os.path.join(self.home, "setup-fails"), "")
        rc, log = self.run_prepare("tester")
        self.assertEqual(rc, 0, log)
        self.assertIn("tpot setup failed, the T-Pot installer runs it again at the first login", log)
        self.again()
        shutil.rmtree(os.path.join(self.share, "wheels"))
        rc, log = self.run_prepare("tester")
        self.assertEqual(rc, 0, log)
        self.assertIn("the ISO carries no wheels", log)
        self.assertIsNone(self.setup_call())

    def test_the_sshd_line_only_where_sshd_takes_it(self):
        drop_in = os.path.join(self.sshd, "tpot.conf")
        for rule, kept in (("ok", True), ("line", False), ("broken", True)):
            with self.subTest(rule=rule):
                if os.path.exists(drop_in):
                    os.remove(drop_in)
                self.run_prepare("nobody", SSHD_RULE=rule)
                self.assertEqual(os.path.exists(drop_in), kept)
                self.assertEqual(os.listdir(self.sshd), ["tpot.conf"] if kept else [])
        write(drop_in, "AcceptEnv LANG\n")
        self.run_prepare("nobody")
        self.assertEqual(base.read(drop_in), "AcceptEnv LANG\n", "a drop-in there stays")


ID_STUB = """#!/bin/sh
case "$1" in
  -u) echo "${STUB_UID:-1000}" ;;
  -un) echo "${STUB_USER:-tester}" ;;
  -Gn) echo "${STUB_GROUPS:-tester cdrom sudo users}" ;;
  *) echo "uid=${STUB_UID:-1000}(${STUB_USER:-tester})" ;;
esac
"""


class StartTest(unittest.TestCase):
    """start.sh runs the install.sh of the ISO with the repository and the branch of iso.conf, which
    it reads and never sources; never as root, never as the user tpot of the containers. Another
    branch (-b, -r or typed at a terminal) is cloned into ~/tpotce and its own install.sh runs; a tag
    or a commit with a warning, a branch the repository does not have never."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.share = os.path.join(self.tmp, "share")
        os.makedirs(self.share)
        shutil.copy(os.path.join(ISO_DIR, "start.sh"), os.path.join(self.share, "start.sh"))
        shutil.copy(os.path.join(ISO_DIR, "terminal.sh"), os.path.join(self.share, "terminal.sh"))
        write(os.path.join(self.share, "install.sh"), '#!/bin/sh\necho "INSTALL $*"\n', 0o755)
        self.bin = os.path.join(self.tmp, "bin")
        write(os.path.join(self.bin, "id"), ID_STUB, 0o755)
        self.conf("TPOT_REPO_URL=https://github.com/telekom-security/tpotce", "TPOT_BRANCH=master")

    def conf(self, *lines):
        write(os.path.join(self.share, "iso.conf"), "# The T-Pot ISO\n" + "\n".join(lines) + "\n")

    def environ(self, **env):
        return {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.tmp, "TERM": "dumb", **env}

    def run_start(self, *args, **env):
        return subprocess.run([BASH, os.path.join(self.share, "start.sh"), *args], capture_output=True, text=True,
                              env=self.environ(**env), cwd=self.tmp, stdin=subprocess.DEVNULL, timeout=60)

    def at_terminal(self, *answers, args=()):
        code, out, _piped = ptyhelper.run([BASH, os.path.join(self.share, "start.sh"), *args], self.environ(),
                                          cwd=self.tmp, answers=answers, timeout=60)
        return code, out.decode("utf-8", "replace").replace("\r\n", "\n")

    def git(self, *args, cwd=None):
        environ = {**self.environ(), "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.org",
                   "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.org"}
        result = subprocess.run(["git", *args], cwd=cwd or self.tmp, capture_output=True, text=True, env=environ,
                                timeout=60)
        self.assertEqual(result.returncode, 0, (args, result.stderr))
        return result.stdout.strip()

    def origin(self, name="origin"):
        """A repository with the branches master and dev and the tag v1 (an older master), each with an
        install.sh that names itself; the ISO is built for its master."""
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        self.git("init", "-q", "-b", "master", cwd=repo)
        for label in ("v1", "master"):
            write(os.path.join(repo, "install.sh"), f'#!/bin/sh\necho "CLONE-{label} $*"\n', 0o755)
            self.git("add", "install.sh", cwd=repo)
            self.git("commit", "-q", "-m", label, cwd=repo)
            if label == "v1":
                self.git("tag", "v1", cwd=repo)
        self.git("checkout", "-q", "-b", "dev", cwd=repo)
        write(os.path.join(repo, "install.sh"), '#!/bin/sh\necho "CLONE-dev $*"\n', 0o755)
        self.git("commit", "-q", "-am", "dev", cwd=repo)
        self.git("checkout", "-q", "master", cwd=repo)
        url = "file://" + repo
        self.conf(f"TPOT_REPO_URL={url}", "TPOT_BRANCH=master")
        return url

    def clone(self):
        return os.path.join(self.tmp, "tpotce")

    def test_hands_over_to_install_sh_with_the_source_of_the_iso(self):
        result = self.run_start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INSTALL -b master -r https://github.com/telekom-security/tpotce", result.stdout)
        self.assertIn("T-Pot from https://github.com/telekom-security/tpotce at master", result.stdout)

    def test_interactive_terminal_handoff_keeps_arguments_and_does_not_recurse(self):
        write(os.path.join(self.share, "terminal.sh"),
              '#!/bin/sh\necho "TERMINAL:${TPOT_ISO_TERMINAL_TRIED}"\n'
              '[ "$1" = -- ] || exit 2\nshift\nexec "$@"\n', 0o755)
        code, out = self.at_terminal(args=("-b", "master", "-r", "https://github.com/telekom-security/tpotce"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("TERMINAL:1"), 1)
        self.assertIn("INSTALL -b master -r https://github.com/telekom-security/tpotce", out)

    def test_a_branch_with_a_slash_and_a_commit(self):
        self.conf("TPOT_REPO_URL=https://github.com/fork/tpotce", "TPOT_BRANCH=feature/iso")
        self.assertIn("INSTALL -b feature/iso -r https://github.com/fork/tpotce", self.run_start().stdout)
        self.conf("TPOT_REPO_URL=https://github.com/fork/tpotce", "TPOT_BRANCH=" + "a" * 40)
        self.assertIn("INSTALL -b " + "a" * 40, self.run_start().stdout)

    def test_iso_conf_is_never_sourced_and_odd_values_are_refused(self):
        marker = os.path.join(self.tmp, "sourced")
        for lines in (("TPOT_REPO_URL=https://github.com/telekom-security/tpotce", f"TPOT_BRANCH=$(touch {marker})"),
                      ("TPOT_REPO_URL=https://github.com/telekom-security/tpotce", "TPOT_BRANCH=--upload-pack=x"),
                      ("TPOT_REPO_URL=-uhttps://x", "TPOT_BRANCH=master"),
                      ("TPOT_REPO_URL=https://x y", "TPOT_BRANCH=master"),
                      ("TPOT_REPO_URL=https://github.com/telekom-security/tpotce",),
                      ()):
            self.conf(*lines)
            result = self.run_start()
            self.assertEqual(result.returncode, 1, lines)
            self.assertNotIn("INSTALL", result.stdout, lines)
            self.assertIn("has no valid", result.stderr, lines)
        self.assertFalse(os.path.exists(marker))

    def test_never_as_root_nor_as_the_user_tpot(self):
        result = self.run_start(STUB_UID="0", STUB_USER="root")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("INSTALL", result.stdout)
        self.assertIn("not as root", result.stderr)
        result = self.run_start(STUB_UID="2000", STUB_USER="tpot")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("INSTALL", result.stdout)
        self.assertIn("reserved for the containers", result.stderr)

    def test_without_a_terminal_it_takes_the_source_of_the_iso_without_asking(self):
        result = self.run_start()
        self.assertNotIn("Branch [", result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.clone()))

    def test_the_help_names_the_source_of_the_iso_and_odd_options_are_refused(self):
        result = self.run_start("-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Enter installs the source\nof this ISO: https://github.com/telekom-security/tpotce at master",
                      result.stdout)
        for args, text in ((("-b", "--upload-pack=x"), "Not a branch, tag or commit: --upload-pack=x"),
                           (("-b", "dev", "-r", "-uhttps://x"), "Not a repository URL: -uhttps://x"),
                           (("-b", "a b"), "Not a branch, tag or commit: a b"),
                           (("-x",), "Unknown option -x"),
                           (("-b",), "Option -b requires an argument"),
                           (("dev",), "Unknown argument: dev")):
            result = self.run_start(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn(text, result.stderr, args)
            self.assertNotIn("INSTALL", result.stdout, args)
        self.assertFalse(os.path.exists(self.clone()))

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_another_branch_is_cloned_and_its_own_installer_runs(self):
        url = self.origin()
        result = self.run_start("-b", "dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"cloning {url} at dev into {self.clone()}", result.stdout)
        self.assertIn(f"CLONE-dev -b dev -r {url}", result.stdout)
        self.assertNotIn("INSTALL", result.stdout)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.clone()), "dev")
        # the source of the ISO by name: the installer of the ISO, nothing cloned
        shutil.rmtree(self.clone())
        result = self.run_start("-b", "master", "-r", url + ".git/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"INSTALL -b master -r {url}", result.stdout)
        self.assertFalse(os.path.exists(self.clone()))

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_a_tag_with_a_warning_a_missing_branch_or_repository_never(self):
        url = self.origin()
        result = self.run_start("-b", "v1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("v1 is a tag or a commit, no branch", result.stdout)
        self.assertIn("update.sh cannot update it (later on a branch again: ./update.sh -y -b master)", result.stdout)
        self.assertIn(f"CLONE-v1 -b v1 -r {url}", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.clone()), self.git("rev-parse", "v1^{commit}",
                                                                                   cwd=os.path.join(self.tmp, "origin")))
        shutil.rmtree(self.clone())
        for args, text in ((("-b", "nope"), f"{url} has no branch nope."),
                           (("-b", "dev", "-r", "file:///nonexistent/tpotce"),
                            "file:///nonexistent/tpotce cannot be reached (git ls-remote ended with 128)")):
            result = self.run_start(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(text, result.stderr, args)
            self.assertIn("T-Pot cannot be installed from", result.stderr, args)
            self.assertNotIn("INSTALL", result.stdout, args)
            self.assertNotIn("CLONE", result.stdout, args)
            self.assertFalse(os.path.exists(self.clone()), args)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_an_existing_clone_only_if_it_is_that_source(self):
        other = self.origin("other")
        url = self.origin()
        self.assertEqual(self.run_start("-b", "dev").returncode, 0)
        result = self.run_start("-b", "dev", "-r", url + "/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{self.clone()} is {url} at dev already.", result.stdout)
        self.assertIn(f"CLONE-dev -b dev -r {url}", result.stdout)
        for args, found in ((("-b", "v1"), f"{url} at v1"), (("-b", "dev", "-r", other), f"{other} at dev")):
            result = self.run_start(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(f"{self.clone()} exists already and is not {found}.", result.stderr)
            self.assertIn(f"Remove it (rm -rf {self.clone()})", result.stderr)
            self.assertNotIn("CLONE", result.stdout, args)
            self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.clone()), "dev")

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_the_t_pot_of_the_iso_makes_way_for_another_source_while_untouched(self):
        url = self.origin()
        self.git("clone", "-q", "-b", "master", url, self.clone())
        commit = self.git("rev-parse", "HEAD", cwd=self.clone())
        self.conf(f"TPOT_REPO_URL={url}", "TPOT_BRANCH=master", f"TPOT_COMMIT={commit}")
        result = self.run_start("-b", "dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"CLONE-dev -b dev -r {url}", result.stdout)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.clone()), "dev")
        self.assertEqual([name for name in os.listdir(self.tmp) if name.startswith("tpotce.")], [])
        # a clone that fails puts it back
        shutil.rmtree(self.clone())
        self.git("clone", "-q", "-b", "master", url, self.clone())
        stub(self.bin, "git", f'[ "$1" != clone ] || exit 128\nexec "{shutil.which("git")}" "$@"\n')
        result = self.run_start("-b", "dev")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Could not clone {url} at dev.", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.clone()), commit)
        self.assertEqual([name for name in os.listdir(self.tmp) if name.startswith("tpotce.")], [])
        os.remove(os.path.join(self.bin, "git"))
        # changed or another commit: it stays, as any other folder there
        write(os.path.join(self.clone(), "mine"), "mine")
        result = self.run_start("-b", "dev")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{self.clone()} exists already and is not {url} at dev.", result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.clone(), "mine")))

    def test_the_wheels_of_the_iso_go_to_tpot_setup(self):
        write(os.path.join(self.share, "install.sh"), '#!/bin/sh\necho "INSTALL $* wheels=${TPOT_WHEELS-none}"\n', 0o755)
        result = self.run_start()
        self.assertIn("wheels=none", result.stdout, result.stderr)
        os.makedirs(os.path.join(self.share, "wheels"))
        result = self.run_start()
        self.assertIn(f"wheels={self.share}/wheels", result.stdout, result.stderr)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_at_a_terminal_enter_takes_the_source_of_the_iso(self):
        url = self.origin()
        code, out = self.at_terminal(("Branch [master]: ", b"--x\n"), ("Branch [master]: ", b"\n"))
        self.assertEqual(code, 0, out)
        self.assertIn(f"T-Pot is installed from {url} at master, the source of this ISO.", out)
        self.assertIn("Not a branch, tag or commit: --x", out)
        self.assertIn(f"INSTALL -b master -r {url}", out)
        self.assertNotIn("Repository [", out)
        self.assertFalse(os.path.exists(self.clone()))

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_at_a_terminal_another_branch_asks_again_until_the_repository_has_it(self):
        url = self.origin()
        code, out = self.at_terminal(("Branch [master]: ", b"nope\n"), (f"Repository [{url}]: ", b"\n"),
                                     ("has no branch nope", b""),
                                     ("Branch [master]: ", b"v1\n"), ("Repository [", b"\n"),
                                     ("Install it anyway? (y/N): ", b"\n"),
                                     ("Branch [master]: ", b"v1\n"), ("Repository [", b"\n"),
                                     ("Install it anyway? (y/N): ", b"y\n"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("Install it anyway?"), 2, out)
        self.assertIn(f"CLONE-v1 -b v1 -r {url}", out)
        shutil.rmtree(self.clone())
        code, out = self.at_terminal(("Branch [master]: ", b"dev\n"), ("Repository [", b"\n"))
        self.assertEqual(code, 0, out)
        self.assertIn(f"CLONE-dev -b dev -r {url}", out)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_at_a_terminal_another_repository_and_ctrl_d_stops(self):
        self.origin()
        fork = "file://" + os.path.join(self.tmp, "fork")
        self.origin("fork")
        self.conf("TPOT_REPO_URL=https://github.com/telekom-security/tpotce", "TPOT_BRANCH=master")
        code, out = self.at_terminal(("Branch [master]: ", b"dev\n"), ("Repository [", fork.encode() + b".git\n"))
        self.assertEqual(code, 0, out)
        self.assertIn(f"CLONE-dev -b dev -r {fork}", out)
        # without .git, as install.sh takes it
        self.assertEqual(self.git("remote", "get-url", "origin", cwd=self.clone()), fork)
        code, out = self.at_terminal(("Branch [master]: ", b"\x04"))
        self.assertEqual(code, 1, out)
        self.assertIn("Stopped, the installer asks again at the next login.", out)
        self.assertNotIn("INSTALL", out)


class ProfileTest(unittest.TestCase):
    """The login hook starts start.sh at an interactive login of a user with sudo, at a terminal, until
    T-Pot is installed; sourced by the login shell, it neither exits it nor leaves variables."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.share = os.path.join(self.tmp, "share")
        self.service = os.path.join(self.tmp, "tpot.service")
        hook = (base.read("installer/iso/profile.sh")
                .replace("/usr/local/share/tpot-iso/", self.share + "/")
                .replace("/etc/systemd/system/tpot.service", self.service))
        self.assertNotIn("/usr/local/share", hook)
        self.assertNotIn("/etc/systemd", hook)
        self.hook = os.path.join(self.tmp, "tpot-iso.sh")
        write(self.hook, hook)
        write(os.path.join(self.share, "iso.conf"), "TPOT_BRANCH=master\n")
        write(os.path.join(self.share, "start.sh"), 'echo "START:${TPOT_ISO_STARTED}"\n', 0o755)
        self.bin = os.path.join(self.tmp, "bin")
        write(os.path.join(self.bin, "id"), ID_STUB, 0o755)

    def login(self, shell=BASH, interactive=True, stdout_tty=True, **env):
        environ = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.tmp, "TERM": "dumb", **env}
        flags = ["--norc"] if shell == BASH else []
        argv = [shell, *flags, *(["-i"] if interactive else []), "-c",
                f'. {self.hook}; echo "AFTER:${{TPOT_ISO_STARTED:-unset}}"']
        _code, out, piped = ptyhelper.run(argv, environ, cwd=self.tmp, stdout_tty=stdout_tty, timeout=30)
        return out.decode("utf-8", "replace") + piped

    def test_starts_the_installer_at_an_interactive_login(self):
        out = self.login()
        self.assertIn("START:1", out)
        # the shell goes on after the hook, without its variable
        self.assertIn("AFTER:unset", out)

    def test_posix_sh_too(self):
        self.assertIn("START:1", self.login(shell=SH))

    def test_not_when_it_should_not(self):
        cases = {"TPOT_ISO=off": dict(TPOT_ISO="off"),
                 "inside the installer": dict(TPOT_ISO_STARTED="1"),
                 "root": dict(STUB_UID="0", STUB_USER="root"),
                 "not interactive": dict(interactive=False),
                 "no terminal": dict(stdout_tty=False)}
        for name, kwargs in cases.items():
            out = self.login(**kwargs)
            self.assertNotIn("START:", out, name)
            self.assertIn("AFTER:", out, name)

    def test_not_once_t_pot_is_installed_or_without_the_iso_conf(self):
        write(self.service, "[Unit]\n")
        self.assertNotIn("START:", self.login())
        os.remove(self.service)
        os.remove(os.path.join(self.share, "iso.conf"))
        self.assertNotIn("START:", self.login())

    def test_a_user_without_sudo_gets_a_hint(self):
        out = self.login(STUB_GROUPS="guest users", STUB_USER="guest")
        self.assertNotIn("START:", out)
        self.assertIn("guest may not use sudo", out)

    def test_the_hint_at_the_login_prompt(self):
        # agetty reads backslash escapes: \4 is the IPv4 address, nothing else
        self.assertEqual(re.findall(r"\\.", base.read("installer/iso/tpot.issue")), ["\\4"])


class IsotreeGrubTest(unittest.TestCase):

    @staticmethod
    def entries(text):
        return re.findall(r"^menuentry (?:--hotkey=\w )?'([^']*)'", text, re.M)

    def test_amd64_the_t_pot_entries_first_with_the_preseed(self):
        out = isotree.patch_grub(GRUB_AMD64)
        self.assertEqual(self.entries(out), ["Install T-Pot", "Install T-Pot (graphical installer)",
                                             "Graphical install", "Install"])
        self.assertIn("menuentry --hotkey=t 'Install T-Pot' {\n    set background_color=black\n"
                      "    linux    /install.amd/vmlinuz vga=788 preseed/file=/cdrom/tpot/preseed.cfg --- quiet \n"
                      "    initrd   /install.amd/initrd.gz\n}", out)
        self.assertIn("menuentry 'Install T-Pot (graphical installer)' {\n    set background_color=black\n"
                      "    linux    /install.amd/vmlinuz vga=788 preseed/file=/cdrom/tpot/preseed.cfg --- quiet \n"
                      "    initrd   /install.amd/gtk/initrd.gz\n}", out)
        # Debian's own entries and the rest stay as they were
        self.assertEqual(out.count("preseed/file"), 2)
        head, tail = GRUB_AMD64.split("menuentry --hotkey=g", 1)
        self.assertTrue(out.startswith(head))
        self.assertTrue(out.endswith("menuentry --hotkey=g" + tail))

    def test_arm64(self):
        out = isotree.patch_grub(GRUB_ARM64)
        self.assertEqual(self.entries(out), ["Install T-Pot", "Install T-Pot (graphical installer)",
                                             "Install", "Graphical install"])
        self.assertIn("    linux    /install.a64/vmlinuz  preseed/file=/cdrom/tpot/preseed.cfg --- quiet\n"
                      "    initrd   /install.a64/initrd.gz", out)

    def test_another_netinst_stops_the_build(self):
        for text, message in ((GRUB_AMD64.replace("'Graphical install'", "'Graphic install'"), "Graphical install"),
                              ("set timeout=5\n" + GRUB_AMD64, "timeout"),
                              (isotree.patch_grub(GRUB_AMD64), "already has T-Pot"),
                              (GRUB_AMD64.replace("vga=788 --- quiet \n    initrd   /install.amd/initrd.gz",
                                                  "vga=788 --- quiet --- \n    initrd   /install.amd/initrd.gz"),
                               "one ' --- '"),
                              (GRUB_AMD64.replace("insmod play", "submenu 'x' {\n}\ninsmod play"), "submenu")):
            with self.assertRaises(isotree.PatchError) as caught:
                isotree.patch_grub(text)
            self.assertIn(message, str(caught.exception))


class IsotreeIsolinuxTest(unittest.TestCase):

    @staticmethod
    def files(**changed):
        files = {"menu.cfg": MENU_CFG, "txt.cfg": TXT_CFG, "gtk.cfg": GTK_CFG, "stdmenu.cfg": STDMENU_CFG,
                 "spkgtk.cfg": SPKGTK_CFG}
        files.update(changed)
        return files

    def test_t_pot_first_and_the_default(self):
        out = isotree.patch_isolinux(self.files())
        self.assertEqual(sorted(out), ["gtk.cfg", "menu.cfg", "spkgtk.cfg", "stdmenu.cfg", "tpot.cfg"])
        self.assertEqual(out["tpot.cfg"],
                         "default tpot\nlabel tpot\n\tmenu label Install ^T-Pot\n\tmenu default\n"
                         "\tkernel /install.amd/vmlinuz\n"
                         "\tappend vga=788 initrd=/install.amd/initrd.gz preseed/file=/cdrom/tpot/preseed.cfg --- quiet\n"
                         "label tpotgui\n\tmenu label Install T-Pot (graphical installer)\n"
                         "\tkernel /install.amd/vmlinuz\n"
                         "\tappend vga=788 initrd=/install.amd/gtk/initrd.gz preseed/file=/cdrom/tpot/preseed.cfg --- quiet\n")
        self.assertIn("include stdmenu.cfg\ninclude tpot.cfg\ninclude gtk.cfg\ninclude txt.cfg\n", out["menu.cfg"])
        # Debian's Graphical install stays, no longer the default
        self.assertEqual(out["gtk.cfg"], GTK_CFG.replace("default installgui\n", "").replace("\tmenu default\n", ""))
        self.assertNotIn("76a1d0", out["stdmenu.cfg"])
        self.assertEqual(out["stdmenu.cfg"].count("#ffe20074"), 2)

    def test_the_timeout_starts_install_t_pot(self):
        out = isotree.patch_isolinux(self.files())
        self.assertEqual(out["spkgtk.cfg"],
                         "label installspk\n\tmenu label Install with ^speech synthesis\n"
                         "\tkernel /install.amd/vmlinuz\n"
                         "\tappend vga=788 initrd=/install.amd/gtk/initrd.gz speakup.synth=soft --- quiet \n"
                         "# timeout to Install T-Pot\ntimeout 300\n"
                         "ontimeout /install.amd/vmlinuz vga=788 initrd=/install.amd/initrd.gz "
                         "preseed/file=/cdrom/tpot/preseed.cfg --- quiet\n"
                         f"menu autoboot {isotree.BIOS_AUTOBOOT}\n")
        # the same command line as the entry Install T-Pot
        tpot = out["tpot.cfg"].split("label tpotgui\n")[0]
        ontimeout = re.search(r"^ontimeout (\S+) (.*)$", out["spkgtk.cfg"], re.M)
        self.assertIn(f"\tkernel {ontimeout.group(1)}\n\tappend {ontimeout.group(2)}\n", tpot)
        # ISOLINUX puts the seconds for the #
        self.assertEqual(isotree.BIOS_AUTOBOOT.count("#"), 1)

    def test_the_title_keeps_its_bel_and_fits_the_menu(self):
        out = isotree.patch_isolinux(self.files())
        self.assertIn(f"menu title \x07{isotree.BIOS_TITLE}\n", out["menu.cfg"])
        # ISOLINUX cuts a title wider than Debian's
        self.assertLessEqual(len(isotree.BIOS_TITLE), len("Debian GNU/Linux installer menu (BIOS mode)"))

    def test_another_netinst_stops_the_build(self):
        for files, message in (
                (self.files(**{"menu.cfg": MENU_CFG.replace("include gtk.cfg\n", "")}), "include gtk.cfg"),
                (self.files(**{"gtk.cfg": GTK_CFG.replace("default installgui\n", "")}), "default installgui"),
                (self.files(**{"txt.cfg": TXT_CFG.replace("label install", "label text")}), "no label install"),
                (self.files(**{"stdmenu.cfg": STDMENU_CFG.replace("#76a1d0ff", "#000000ff")}), "#76a1d0ff"),
                ({"menu.cfg": MENU_CFG, "txt.cfg": TXT_CFG, "gtk.cfg": GTK_CFG, "spkgtk.cfg": SPKGTK_CFG},
                 "stdmenu.cfg is missing"),
                ({"menu.cfg": MENU_CFG, "txt.cfg": TXT_CFG, "gtk.cfg": GTK_CFG, "stdmenu.cfg": STDMENU_CFG},
                 "spkgtk.cfg is missing"),
                (self.files(**{"spkgtk.cfg": SPKGTK_CFG.replace("timeout 300\n", "")}), "no timeout"),
                (self.files(**{"spkgtk.cfg": SPKGTK_CFG.replace("speakup.synth=soft --- quiet \nmenu",
                                                                "--- quiet \nmenu")}),
                 "one ontimeout to the speech synthesis"),
                (self.files(**{"spkgtk.cfg": SPKGTK_CFG + "ontimeout installspk\n"}),
                 "one ontimeout to the speech synthesis"),
                (self.files(**{"spkgtk.cfg": SPKGTK_CFG.replace("will be started", "starts")}),
                 "menu autoboot Press a key"),
                (self.files(**{"spkgtk.cfg": SPKGTK_CFG.replace("# timeout to", "# time out to")}),
                 "# timeout to speech-enabled install")):
            with self.assertRaises(isotree.PatchError) as caught:
                isotree.patch_isolinux(files)
            self.assertIn(message, str(caught.exception))

    def test_the_themes_with_the_background(self):
        out = isotree.patch_theme(THEME, "1")
        self.assertIn('title-text: ""', out)
        self.assertIn(f"+ vbox {{\n  top = {isotree.LABEL_TOP}\n  left = {isotree.LABEL_LEFT}\n", out)
        self.assertIn('"T-Pot installer on Debian GNU/Linux 13.7.0 (UEFI)"', out)
        self.assertIn('selected_item_color = "white"', out)
        self.assertIsNone(isotree.patch_theme(DARK_THEME, "dark-1"))
        with self.assertRaises(isotree.PatchError):
            isotree.patch_theme(THEME.replace("top = 80", "top = 90"), "1")


class IsotreeFilesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_the_splash_the_logo_top_centre_on_black(self):
        width, height, rows = png_pixels(isotree.splash_png())
        self.assertEqual((width, height), isotree.SPLASH_SIZE)
        lit = [y for y, row in enumerate(rows) if row.strip(b"\0")]
        # the logo above the menus (ISOLINUX starts them at about row 150), nothing below it
        self.assertGreaterEqual(lit[0], isotree.SPLASH_TOP)
        self.assertLess(lit[-1], 140)
        columns = [x for row in rows for x in range(width) if row[x * 3:x * 3 + 3] != b"\0\0\0"]
        self.assertAlmostEqual((min(columns) + max(columns)) / 2, width / 2, delta=4)
        self.assertIn(bytes(isotree.MAGENTA), b"".join(rows))

    def test_the_highlight_in_the_size_of_debian_s(self):
        width, height, rows = png_pixels(isotree.solid_png(20, 20))
        self.assertEqual((width, height), (20, 20))
        self.assertEqual(set(rows), {bytes(isotree.MAGENTA) * 20})
        self.assertEqual(isotree.png_size(isotree.solid_png(7, 3)), (7, 3))
        with self.assertRaises(isotree.PatchError):
            isotree.png_size(b"GIF89a" + b"\0" * 30)

    def test_md5sum_lines_updated_and_added(self):
        text = f"{'0' * 32}  ./boot/grub/grub.cfg\n{'1' * 32}  ./install.amd/vmlinuz\n"
        out = isotree.update_md5sum(text, {"boot/grub/grub.cfg": b"new", "tpot/late.sh": b"late"})
        self.assertEqual(out, f"{hashlib.md5(b'new').hexdigest()}  ./boot/grub/grub.cfg\n"
                              f"{'1' * 32}  ./install.amd/vmlinuz\n"
                              f"{hashlib.md5(b'late').hexdigest()}  ./tpot/late.sh\n")
        with self.assertRaises(isotree.PatchError):
            isotree.update_md5sum("not a line\n", {})

    def test_patch_tree_amd64(self):
        orig, out = os.path.join(self.tmp, "orig"), os.path.join(self.tmp, "out")
        netinst_tree(orig, "amd64")
        written = isotree.patch_tree("amd64", orig, out)
        self.assertEqual(written, ["boot/grub/grub.cfg", "boot/grub/theme/1", "boot/grub/theme/1-1",
                                   "boot/grub/theme/hl_c.png", "isolinux/gtk.cfg", "isolinux/menu.cfg",
                                   "isolinux/spkgtk.cfg", "isolinux/splash.png", "isolinux/stdmenu.cfg",
                                   "isolinux/tpot.cfg"])
        self.assertEqual(sorted(isotree.tree_files(out)), written)
        with open(os.path.join(out, "boot/grub/theme/hl_c.png"), "rb") as handle:
            self.assertEqual(isotree.png_size(handle.read()), (20, 20))

    def test_patch_tree_arm64_graphics_with_text_fallback(self):
        orig, out = os.path.join(self.tmp, "orig"), os.path.join(self.tmp, "out")
        netinst_tree(orig, "arm64")
        self.assertEqual(isotree.patch_tree("arm64", orig, out),
                         ["boot/grub/grub.cfg", "boot/grub/tpot/hl_c.png",
                          "boot/grub/tpot/splash.png", "boot/grub/tpot/theme.txt"])
        with open(os.path.join(out, "boot/grub/grub.cfg")) as handle:
            grub = handle.read()
        self.assertIn("set tpot_font=/boot/grub/font.pf2", grub)
        self.assertIn("if [ -f (memdisk)/fonts/unicode.pf2 ]; then\n"
                      "  set tpot_font=(memdisk)/fonts/unicode.pf2\nfi\nif loadfont $tpot_font; then", grub)
        self.assertIn("if terminal_output gfxterm; then\n    set theme=/boot/grub/tpot/theme.txt", grub)
        self.assertIn("set menu_color_highlight=white/magenta", grub)
        self.assertNotIn("set timeout=", grub)
        self.assertNotIn("terminal_output console", grub)
        self.assertIn(GRUB_ARM64[GRUB_ARM64.index("menuentry 'Install'"):], grub)
        with open(os.path.join(out, "boot/grub/tpot/theme.txt")) as handle:
            theme = handle.read()
        self.assertIn('desktop-image: "/boot/grub/tpot/splash.png"', theme)
        self.assertIn('item_font = "GNU Unifont Regular 16"', theme)
        self.assertIn('selected_item_pixmap_style = "hl_*.png"', theme)

    def test_arm64_needs_the_debian_grub_font(self):
        orig, out = os.path.join(self.tmp, "orig"), os.path.join(self.tmp, "out")
        netinst_tree(orig, "arm64")
        os.remove(os.path.join(orig, "boot/grub/font.pf2"))
        with self.assertRaisesRegex(isotree.PatchError, "font.pf2 is missing"):
            isotree.patch_tree("arm64", orig, out)

    def test_the_cli_says_what_is_wrong(self):
        orig, out = os.path.join(self.tmp, "orig"), os.path.join(self.tmp, "out")
        netinst_tree(orig, "amd64")
        os.remove(os.path.join(orig, "boot/grub/theme/hl_c.png"))
        result = subprocess.run(["python3", os.path.join(ISO_DIR, "isotree.py"), "patch", "--arch", "amd64",
                                 "--orig", orig, "--out", out], capture_output=True, text=True, timeout=60,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("isotree.py: boot/grub/theme/hl_c.png is missing", result.stderr)


class IsotreeRequirementsTest(unittest.TestCase):
    """isotree.py requirements: tpotctl/requirements.txt for the python3 of Debian, as pip download of
    build.sh needs it (pip decides markers for the python it runs with, not for --python-version)."""

    def test_the_requirements_for_the_python_of_debian(self):
        text = base.read("tpotctl/requirements.txt")
        out = isotree.requirements_for(text, isotree.marker_env("3.13", "x86_64"))
        lines = out.splitlines()
        names = [line.split("==")[0] for line in lines]
        self.assertEqual(len(names), len(set(names)), out)
        self.assertIn("textual", names)
        self.assertIn("pyyaml", names)
        for line in lines:
            self.assertNotIn(";", line)
            self.assertNotIn("#", line.replace("--hash=sha256:", ""))
            self.assertRegex(line, r"^[a-z0-9._-]+==\S+( --hash=sha256:[0-9a-f]{64})+$")
        # every requirement without a marker, and every one with a marker that holds for 3.13
        self.assertIn("linkify-it-py==2.2.0 ", out)
        self.assertNotIn("linkify-it-py==2.0.3", out)
        old = isotree.requirements_for(text, isotree.marker_env("3.9", "aarch64"))
        self.assertIn("linkify-it-py==2.0.3 ", old)

    def test_markers(self):
        env = isotree.marker_env("3.13", "aarch64")
        for marker, expected in (("python_full_version < '3.10'", False), ("python_full_version >= '3.10'", True),
                                 ("python_full_version >= '3.13'", True), ("python_full_version < '3.13'", False),
                                 ("python_version >= '3.13'", True), ("'3.10' <= python_version", True),
                                 ("sys_platform == 'win32' or platform_machine == 'aarch64'", True),
                                 ("(sys_platform == 'linux' and python_version < '3.12') or os_name == 'nt'", False),
                                 ("platform_machine in 'x86_64 aarch64'", True),
                                 ('platform_machine not in "x86_64"', True)):
            with self.subTest(marker=marker):
                self.assertEqual(isotree.marker_true(marker, env), expected)
        for marker in ("python_full_version > '3.13'", "python_full_version == '3.13.2'", "python_version ~= '3.13'",
                       "extra == 'yaml'", "python_version <", "(python_version < '3.10'", "python_version < 3.10",
                       "python_version < '3.10' sys_platform"):
            with self.subTest(marker=marker), self.assertRaises(isotree.MarkerError):
                isotree.marker_true(marker, env)

    def test_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "requirements.txt")
            write(path, "a==1 ; python_version < '3.10' \\\n    --hash=sha256:" + "1" * 64 + "\n    # via b\n"
                        "c==2 \\\n    --hash=sha256:" + "2" * 64 + " \\\n    --hash=sha256:" + "3" * 64 + "\n")
            result = subprocess.run([sys.executable, os.path.join(ISO_DIR, "isotree.py"), "requirements", "--python",
                                     "3.13", "--machine", "aarch64", path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"c==2 --hash=sha256:{'2' * 64} --hash=sha256:{'3' * 64}\n")
            write(path, "a==1 ; extra == 'x' \\\n    --hash=sha256:" + "1" * 64 + "\n")
            result = subprocess.run([sys.executable, os.path.join(ISO_DIR, "isotree.py"), "requirements", "--python",
                                     "3.13", "--machine", "aarch64", path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unknown marker extra", result.stderr)


@unittest.skipUnless(shutil.which("git"), "no git")
class IsotreeGitTarTest(unittest.TestCase):
    """isotree.py gittar: the .git of the snapshot as the tar prepare.sh unpacks, owned by root, in a
    fixed order, without extended attributes (macOS), nothing but files, folders and links."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.snap = os.path.join(self.tmp, "snap")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=master"]
        subprocess.run(git + ["init", "-q", self.snap], check=True)
        write(os.path.join(self.snap, "version"), "24.04.2\n")
        subprocess.run(git + ["-C", self.snap, "add", "-A"], check=True)
        subprocess.run(git + ["-C", self.snap, "commit", "-qm", "T-Pot"], check=True)
        os.symlink("HEAD", os.path.join(self.snap, ".git", "head-link"))
        if shutil.which("xattr"):
            subprocess.run(["xattr", "-w", "com.example.tpot", "x", os.path.join(self.snap, ".git", "HEAD")],
                           check=True)

    def gittar(self, out):
        return subprocess.run([sys.executable, os.path.join(ISO_DIR, "isotree.py"), "gittar", "--dir", self.snap, out],
                              capture_output=True, text=True)

    def test_the_tar_of_the_snapshot(self):
        out = os.path.join(self.tmp, "tpotce.tar")
        result = self.gittar(out)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(out) as tar:
            members = tar.getmembers()
        names = [member.name for member in members]
        self.assertEqual(names[0], ".git")
        self.assertTrue(all(name == ".git" or name.startswith(".git/") for name in names))
        self.assertIn(".git/head-link", names)
        self.assertTrue(all((m.uid, m.gid, m.uname, m.gname) == (0, 0, "root", "root") for m in members))
        self.assertEqual([m.name for m in members if m.pax_headers], [], "no extended attributes")
        # every folder before what is in it, the same order again
        seen = set()
        for name in names:
            self.assertIn(os.path.dirname(name) or ".", seen | {"."}, name)
            seen.add(name)
        again = os.path.join(self.tmp, "again.tar")
        self.gittar(again)
        with tarfile.open(again) as tar:
            self.assertEqual(tar.getnames(), names)
        # the tar of the system unpacks it, git makes the checkout from it
        home = os.path.join(self.tmp, "tpotce")
        os.makedirs(home)
        result = subprocess.run(["tar", "-xf", out, "-C", home], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        subprocess.run(["git", "-C", home, "reset", "-q", "--hard"], check=True)
        self.assertEqual(base.read(os.path.join(home, "version")), "24.04.2\n")

    def test_no_git_folder_or_a_fifo_stops_it(self):
        os.mkfifo(os.path.join(self.snap, ".git", "fifo"))
        result = self.gittar(os.path.join(self.tmp, "a.tar"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is no file, folder or link", result.stderr)
        shutil.rmtree(os.path.join(self.snap, ".git"))
        result = self.gittar(os.path.join(self.tmp, "b.tar"))
        self.assertEqual(result.returncode, 1)
        self.assertIn(".git is no folder", result.stderr)


class BuildTest(unittest.TestCase):
    """build.sh: its options, the checks of the netinst and of the ISO (functions alone with stubs)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)

    def build(self, *args):
        environ = {**os.environ, "TPOT_GUM": "off", "NO_COLOR": "1", "HOME": self.tmp}
        environ.pop("TPOT_BRANCH", None)
        environ.pop("TPOT_REPO_URL", None)
        return subprocess.run([BASH, BUILD, *args], capture_output=True, text=True, env=environ, timeout=60)

    def bash(self, script, **env):
        environ = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.tmp, **env}
        return subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=environ, timeout=60)

    def test_help(self):
        result = self.build("-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("-a <arch>", "-b <branch>", "-r <repo-url>", "-i <netinst>", "-o <folder>", "-D"):
            self.assertIn(option, result.stdout)

    def test_wrong_options_exit_2_before_anything_is_done(self):
        netinst = os.path.join(self.tmp, "debian-13.7.0-amd64-netinst.iso")
        wrong = os.path.join(self.tmp, "ubuntu.iso")
        write(netinst, "")
        write(wrong, "")
        out = os.path.join(self.tmp, "out")
        for args in (["-x"], ["-a", "sparc"], ["-a"], ["-o", out, "extra"], ["-a", "amd64", "-i", "/nonexistent.iso"],
                     ["-a", "amd64", "-i", wrong], ["-a", "arm64", "-i", netinst],
                     ["-a", "amd64", "-b", "--upload-pack=x", "-r", "https://github.com/x/y"],
                     ["-a", "amd64", "-b", "master", "-r", "https://x y"]):
            result = self.build("-o", out, *args)
            self.assertEqual(result.returncode, 2, (args, result.stdout, result.stderr))
        self.assertFalse(os.path.exists(out))

    def test_the_debian_of_the_t_pot_installer(self):
        major = re.search(r'^myDEBIAN_MAJOR="(\d+)"$', base.read("installer/iso/build.sh"), re.M).group(1)
        install = re.search(r'"Debian GNU/Linux"\)\n\s+mySUPPORTED_VERSION="(\d+)"',
                            base.read("install.sh")).group(1)
        self.assertEqual(major, install)

    def test_the_payload_is_what_late_sh_needs(self):
        payload = re.search(r'^myPAYLOAD="([^"]+)"$', base.read("installer/iso/build.sh"), re.M).group(1).split()
        for name in payload:
            self.assertTrue(os.path.isfile(os.path.join(ISO_DIR, name)), name)
        needed = re.search(r"^for myFILE in ([^;]+); do$", base.read("installer/iso/late.sh"), re.M).group(1).split()
        self.assertLessEqual(set(needed), set(payload) | {"install.sh", "version", "iso.conf", "tpotce.tar"})
        for name in ("early.sh", "late.sh", "start.sh", "terminal.sh", "prepare.sh", "build.sh"):
            self.assertTrue(os.access(os.path.join(ISO_DIR, name), os.X_OK), name)

    def test_build_and_start_accept_the_same_source(self):
        pattern = re.compile(r"=~ (\^\[A-Za-z0-9\]\S+\$) \]\]")
        found = pattern.findall(base.read("installer/iso/start.sh"))
        self.assertEqual(len(found), 2)
        self.assertEqual(pattern.findall(base.read("installer/iso/build.sh")), found)

    def test_the_boot_records_without_their_blocks(self):
        write(os.path.join(self.bin, "xorriso"), '#!/bin/sh\ncat "$2"\n', 0o755)
        records = {}
        for name, report in (("old", REPORT), ("new", REPORT_MOVED),
                             ("other", REPORT.replace("MBR isohybrid cyl-align-on GPT APM", "MBR cyl-align-on GPT"))):
            path = os.path.join(self.tmp, name)
            write(path, report)
            result = self.bash(function("fuBOOT_RECORDS") + f'fuBOOT_RECORDS "{path}"')
            self.assertEqual(result.returncode, 0, result.stderr)
            records[name] = result.stdout
        self.assertIn("El Torito boot img : 1 BIOS y none 0x0000 0x00 4\n", records["old"])
        self.assertIn("El Torito img path : 2 /boot/grub/efi.img", records["old"])
        self.assertIn("MBR partition : 2 0x00 0xef\n", records["old"])
        self.assertNotIn("2869", records["old"])
        self.assertEqual(records["old"], records["new"])
        self.assertNotEqual(records["old"], records["other"])

    def test_the_netinst_only_with_the_signature_of_the_pinned_key(self):
        key = re.search(r'^myDEBIAN_KEY="([0-9A-F]{40})"$', base.read("installer/iso/build.sh"), re.M).group(1)
        web = os.path.join(self.tmp, "web")
        write(os.path.join(web, "SHA512SUMS"), "sums\n")
        write(os.path.join(web, "SHA512SUMS.sign"), "sign\n")
        write(os.path.join(self.tmp, "work", "debian-cd.key"), "key\n")
        write(os.path.join(self.bin, "curl"),
              '#!/bin/sh\nwhile [ $# -gt 1 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
              f'cp "{web}/$(basename "$1")" "$out"\n', 0o755)
        write(os.path.join(self.bin, "gpg"),
              '#!/bin/sh\ncase " $* " in *" --dearmor "*) cat; exit 0 ;; esac\n'
              'printf "%b" "$STUB_STATUS"\nexit "${STUB_RC:-0}"\n', 0o755)
        script = (f'myWORK="{self.tmp}/work"; myDEBIAN_KEY="{key}"\n' + function("fuFETCH") + function("fuSUMS")
                  + f'fuSUMS https://cdimage.example/iso-cd "{self.tmp}/sums"')
        other = "0123456789ABCDEF0123456789ABCDEF01234567"
        cases = {
            f"[GNUPG:] GOODSIG DA87E80D6294BE9B Debian\\n[GNUPG:] VALIDSIG {key} 2026-09-12 0 0 4 0 1 8 00 {key}\\n": 0,
            # a subkey signed it: the primary key is the last field
            f"[GNUPG:] VALIDSIG {other} 2026-09-12 0 0 4 0 1 8 00 {key}\\n": 0,
            f"[GNUPG:] VALIDSIG {other} 2026-09-12 0 0 4 0 1 8 00 {other}\\n": 1,
            "[GNUPG:] BADSIG DA87E80D6294BE9B Debian\\n": 1,
            "[GNUPG:] GOODSIG DA87E80D6294BE9B Debian\\n": 1,
            f"[GNUPG:] ERRSIG DA87E80D6294BE9B 1 8 00 0 9 {key}\\n[GNUPG:] NO_PUBKEY DA87E80D6294BE9B\\n": 1,
            f"[GNUPG:] VALIDSIG {key}0 2026-09-12\\n": 1,
        }
        for status, code in cases.items():
            result = self.bash(script, STUB_STATUS=status)
            self.assertEqual(result.returncode, code, (status, result.stdout, result.stderr))
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "sums"))), ["SHA512SUMS", "SHA512SUMS.sign"])

    def test_no_plain_fallback_it_needs_the_checkout(self):
        script = base.read("installer/iso/build.sh")
        self.assertNotIn("# >>> plain fallback", script)
        self.assertIn('source "${myREPO}/installer/lib/ui.sh"', script)


REAL_DD = shutil.which("dd") or "/bin/dd"

# diskutil of macOS: list -plist external physical, list -plist <disk>, info -plist <disk>
DISKUTIL = '''import json, plistlib, sys
DATA = json.loads(%r)
args = sys.argv[1:]
if args == ["list", "-plist", "external", "physical"]:
    out = {"WholeDisks": DATA["whole"]}
elif len(args) == 3 and args[:2] == ["list", "-plist"]:
    out = {"VolumesFromDisks": DATA["volumes"].get(args[2], [])}
elif len(args) == 3 and args[:2] == ["info", "-plist"]:
    out = DATA["disks"].get(args[2].replace("/dev/", "", 1))
    if out is None:
        sys.exit(1)
else:
    sys.exit(1)
sys.stdout.buffer.write(plistlib.dumps(out))
'''

# plutil -extract <key.path> raw -o - - of macOS, where there is none
PLUTIL = '''import plistlib, sys
data = plistlib.loads(sys.stdin.buffer.read())
for key in sys.argv[2].split("."):
    try:
        data = data[int(key)] if isinstance(data, list) else data[key]
    except (KeyError, IndexError, ValueError, TypeError):
        sys.exit(1)
if isinstance(data, (dict, list)):
    sys.exit(1)
print(("true" if data else "false") if isinstance(data, bool) else data)
'''


def stub(folder, name, body):
    write(os.path.join(folder, name), "#!/bin/sh\n" + body, 0o755)


def iso_with_sums(path, data):
    """An ISO with its .sha256 and .sha512 as build.sh writes them."""
    write(path, data)
    name = os.path.basename(path)
    write(path + ".sha256", f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    write(path + ".sha512", f"{hashlib.sha512(data).hexdigest()}  {name}\n")


class BuildToolTest(unittest.TestCase):
    """build.sh as a tool: the options of -w, -d, -l and -R, the USB sticks it may write to (stubs of
    lsblk, findmnt, diskutil, df), writing a stick (a file here) and reading it back, the checksums,
    the checks, the signature and the upload of a release, the cache of the netinst, gum for macOS
    and the menu at a terminal. The runs that write use a copy of installer/iso, never the checkout."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(self.bin)
        os.makedirs(self.stubs)

    def env(self, **extra):
        environ = {key: value for key, value in os.environ.items()
                   if key not in ("TPOT_BRANCH", "TPOT_REPO_URL", "TPOT_ISO_CACHE", "TPOT_ISO_SIGN_KEY",
                                  "XDG_DATA_HOME", "TPOT_ISO_IN_DOCKER")}
        environ.update({"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.tmp, "TMPDIR": self.tmp,
                        "TPOT_GUM": "off", "NO_COLOR": "1"})
        environ.update(extra)
        return environ

    def source(self, script, **extra):
        """The script after build.sh is sourced (its functions, nothing runs)."""
        return subprocess.run([BASH, "-c", f'source "{BUILD}"\n{script}'], capture_output=True, text=True,
                              env=self.env(**extra), stdin=subprocess.DEVNULL, timeout=60)

    def tree(self):
        """build.sh in a copy of what it needs (installer/iso without out, ui.sh, version), no git."""
        repo = os.path.join(self.tmp, "repo")
        shutil.copytree(ISO_DIR, os.path.join(repo, "installer", "iso"),
                        ignore=shutil.ignore_patterns("out", "__pycache__"))
        os.makedirs(os.path.join(repo, "installer", "lib"))
        shutil.copy(os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(repo, "installer", "lib"))
        shutil.copy(os.path.join(REPO, "version"), repo)
        return os.path.join(repo, "installer", "iso", "build.sh")

    def run_build(self, build, *args, **extra):
        return subprocess.run([BASH, build, *args], capture_output=True, text=True, env=self.env(**extra),
                              stdin=subprocess.DEVNULL, timeout=60)

    def linux(self, disks, mounts, nodes=None):
        """uname, lsblk, findmnt and readlink of a Linux. disks: the lines of lsblk -dpnbP; mounts: path
        to source (findmnt --target, the longest path wins); nodes: what lsblk -nrpo / -nsrpo <columns>
        <device> answers, by <columns><device with _ for />."""
        write(os.path.join(self.stubs, "disks"), "".join(f"{line}\n" for line in disks))
        write(os.path.join(self.stubs, "mounts"), "".join(f"{path} {src}\n" for path, src in mounts.items()))
        for name, text in (nodes or {}).items():
            write(os.path.join(self.stubs, name), text)
        stub(self.bin, "uname", 'case "$1" in -m) echo x86_64 ;; *) echo Linux ;; esac\n')
        stub(self.bin, "lsblk", f'S="{self.stubs}"\n'
             'case "$1" in\n'
             '  -dpnbP) cat "$S/disks" ;;\n'
             '  -nrpo|-nsrpo) f="$S/$2$(printf %s "$3" | tr / _)"; [ ! -f "$f" ] || cat "$f" ;;\n'
             'esac\n')
        stub(self.bin, "findmnt", 'awk -v p="$4" \'index(p, $1) == 1 && length($1) > n { n = length($1); s = $2 }'
             f' END {{ if (s != "") print s }}\' "{self.stubs}/mounts"\n')
        stub(self.bin, "readlink", 'while [ "${1#-}" != "$1" ]; do shift; done\necho "$1"\n')
        stub(self.bin, "udisksctl", "exit 1\n")

    def darwin(self, disks, whole, volumes, mounts):
        """uname, diskutil and df of a Mac (and plutil where there is none)."""
        write(os.path.join(self.stubs, "mounts"), "".join(f"{path} {src}\n" for path, src in mounts.items()))
        write(os.path.join(self.bin, "diskutil"), f"#!{sys.executable}\n"
              + DISKUTIL % json.dumps({"disks": disks, "whole": whole, "volumes": volumes}), 0o755)
        if not (sys.platform == "darwin" and os.path.exists("/usr/bin/plutil")):
            write(os.path.join(self.bin, "plutil"), f"#!{sys.executable}\n" + PLUTIL, 0o755)
        stub(self.bin, "uname", 'case "$1" in -m) echo arm64 ;; *) echo Darwin ;; esac\n')
        stub(self.bin, "df", 'echo "Filesystem 512-blocks Used Available Capacity Mounted on"\n'
             's=$(awk -v p="$2" \'index(p, $1) == 1 && length($1) > n { n = length($1); s = $2 } END { print s }\' '
             f'"{self.stubs}/mounts")\necho "$s 1 1 1 1% /"\n')

    def stick(self, size=8 * 1024 * 1024, listed=None):
        """A USB stick of a Linux that is a file of zeros; dd without O_DIRECT (tmpfs has none)."""
        path = os.path.join(self.tmp, "stick")
        write(path, b"\0" * size)
        self.linux([f'NAME="{path}" SIZE="{listed or size}" TYPE="disk" RM="1" RO="0" TRAN="usb" VENDOR="Test" '
                    'MODEL="Stick"'], {"/": "/dev/sda2"}, {"NAME,TYPE_dev_sda2": "/dev/sda2 part\n/dev/sda disk\n"})
        stub(self.bin, "dd", 'for a in "$@"; do shift; case "$a" in iflag=direct|oflag=direct) ;; '
             f'*) set -- "$@" "$a" ;; esac; done\nexec "{REAL_DD}" "$@"\n')
        return path

    def head(self, path, size):
        with open(path, "rb") as handle:
            return handle.read(size)

    def test_the_options_of_writing_and_of_a_release(self):
        result = self.run_build(BUILD, "-h")
        for option in ("-w <iso>", "-d <device>", "-l", "-y", "-R", "-s <gpg-key>", "-U <tag>"):
            self.assertIn(option, result.stdout)
        iso = os.path.join(self.tmp, "a.iso")
        netinst = os.path.join(self.tmp, "debian-13.7.0-amd64-netinst.iso")
        write(iso, "iso")
        write(netinst, "")
        out = os.path.join(self.tmp, "out")
        for args in (["-l", "-y"], ["-l", "-a", "amd64"], ["-o", out, "-s", "KEY"], ["-o", out, "-U", "24.04.2"],
                     ["-o", out, "-a", "both", "-d", "nostick", "-y"], ["-o", out, "-R", "-d", "nostick", "-y"],
                     ["-o", out, "-a", "amd64", "-d", "nostick"], ["-o", out, "-a", "amd64", "-d", "nostick", "-y"],
                     ["-o", out, "-a", "both", "-i", netinst], ["-o", out, "-R", "-U", "no tag"],
                     ["-w", os.path.join(self.tmp, "none.iso"), "-d", "nostick", "-y"], ["-w", iso],
                     ["-w", iso, "-d", "nostick", "-y"], ["-w", iso, "-a", "amd64", "-d", "nostick", "-y"]):
            result = self.run_build(BUILD, *args)
            self.assertEqual(result.returncode, 2, (args, result.stdout, result.stderr))
        self.assertFalse(os.path.exists(out))

    def test_the_usb_sticks_of_linux(self):
        out = os.path.join(self.tmp, "iso", "out")
        media = os.path.join(self.tmp, "media")
        os.makedirs(os.path.join(out, "cache"))
        os.makedirs(media)
        marker = os.path.join(self.tmp, "pwned")
        self.linux([
            'NAME="/dev/sda" SIZE="500107862016" TYPE="disk" RM="0" RO="0" TRAN="sata" VENDOR="ATA     " '
            'MODEL="Samsung SSD 870"',
            'NAME="/dev/sdb" SIZE="32015679488" TYPE="disk" RM="1" RO="0" TRAN="usb" VENDOR="SanDisk " '
            'MODEL="Cruzer Blade"',
            # the disk of the ISOs, the root of a system booted from USB, an empty card reader, write-protected
            'NAME="/dev/sdc" SIZE="1000204886016" TYPE="disk" RM="0" RO="0" TRAN="usb" VENDOR="Samsung" MODEL="T7"',
            'NAME="/dev/sdg" SIZE="64023257088" TYPE="disk" RM="0" RO="0" TRAN="usb" VENDOR="Boot" MODEL="SSD"',
            'NAME="/dev/sdd" SIZE="0" TYPE="disk" RM="1" RO="0" TRAN="usb" VENDOR="Generic" MODEL="Reader"',
            'NAME="/dev/sde" SIZE="8000000000" TYPE="disk" RM="1" RO="1" TRAN="usb" VENDOR="Lock" MODEL="Stick"',
            # lsblk writes $ as \x24: read as text, never run
            f'NAME="/dev/sdf" SIZE="4000000000" TYPE="disk" RM="1" RO="0" TRAN="" VENDOR="" '
            f'MODEL="x\\x24(touch {marker})"',
            'NAME="/dev/mmcblk0" SIZE="15931539456" TYPE="disk" RM="0" RO="0" TRAN="mmc" VENDOR="" MODEL=""',
            'NAME="/dev/sr0" SIZE="1073741312" TYPE="rom" RM="1" RO="0" TRAN="sata" VENDOR="HL-DT-ST" MODEL="DVD"',
        ], {"/": "/dev/sdg2", self.tmp: "/dev/sda3[/@home]", out: "/dev/sdc1", media: "/dev/sdb1"}, {
            "LABEL_dev_sdb": "\nSTICK\nMY\\x20DATA\n",
            "NAME,TYPE_dev_sda3": "/dev/sda3 part\n/dev/sda disk\n",
            "NAME,TYPE_dev_sdb1": "/dev/sdb1 part\n/dev/sdb disk\n",
            "NAME,TYPE_dev_sdc1": "/dev/sdc1 part\n/dev/sdc disk\n",
            "NAME,TYPE_dev_sdg2": "/dev/sdg2 part\n/dev/sdg disk\n",
        })
        script = (f'myDIR="{self.tmp}/iso" myCACHE="{out}/cache" myOUT=""\n'
                  'fuUSB_LIST; echo --; fuPROTECTED; echo --\n'
                  'for d in sdb /dev/sdb sda sdc sdg sdd sde sr0 mmcblk0; do\n'
                  '  if fuUSB_FIND "$d" >/dev/null; then echo "$d yes"; else echo "$d no"; fi\n'
                  'done\n'
                  f'fuUSB_FIND sdb "{media}" || echo "the stick of the ISO no"\n')
        result = self.source(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        sticks, protected, found = result.stdout.split("--\n")
        self.assertEqual(sticks, "/dev/sdb|32015679488|SanDisk Cruzer Blade · 32.0 GB · USB · /dev/sdb · STICK, MY DATA\n"
                         f"/dev/sdf|4000000000|x$(touch {marker}) · 4.0 GB · removable · /dev/sdf\n"
                         "/dev/mmcblk0|15931539456|Drive · 15.9 GB · SD card · /dev/mmcblk0\n")
        self.assertEqual(protected, "/dev/sda\n/dev/sdc\n/dev/sdg\n")
        self.assertEqual(found, "sdb yes\n/dev/sdb yes\nsda no\nsdc no\nsdg no\nsdd no\nsde no\nsr0 no\n"
                         "mmcblk0 yes\nthe stick of the ISO no\n")
        self.assertFalse(os.path.exists(marker))

    def test_the_usb_sticks_of_macos(self):
        out = os.path.join(self.tmp, "iso", "out")
        os.makedirs(os.path.join(out, "cache"))
        stick = {"Internal": False, "VirtualOrPhysical": "Physical", "WritableMedia": True}
        disks = {
            "disk0": {"ParentWholeDisk": "disk0", "Internal": True},
            "disk0s2": {"ParentWholeDisk": "disk0"},
            # the APFS container of the system on a partition of disk0
            "disk3": {"ParentWholeDisk": "disk3", "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}]},
            "disk3s1s1": {"ParentWholeDisk": "disk3"},
            "disk4": {**stick, "Size": 32015679488, "MediaName": "SanDisk Ultra  ", "BusProtocol": "USB"},
            "disk5": {**stick, "VirtualOrPhysical": "Virtual", "Size": 1000000000, "MediaName": "Disk Image"},
            "disk6": {**stick, "WritableMedia": False, "Size": 8000000000, "MediaName": "Locked SD"},
            # the disk of the ISOs
            "disk7": {**stick, "Size": 1000204886016, "MediaName": "Samsung T7", "BusProtocol": "USB"},
            "disk7s1": {"ParentWholeDisk": "disk7"},
            "disk8": {**stick, "TotalSize": 63864569856, "MediaName": "Apple SDXC Reader",
                      "BusProtocol": "Secure Digital"},
            "disk9": {**stick, "Internal": True, "Size": 2000398934016, "MediaName": "Internal HDD"},
        }
        self.darwin(disks, ["disk4", "disk5", "disk6", "disk7", "disk8", "disk9"], {"disk4": ["STICK"]},
                    {"/": "/dev/disk3s1s1", out: "/dev/disk7s1"})
        script = (f'myDIR="{self.tmp}/iso" myCACHE="{out}/cache" myOUT=""\n'
                  'fuUSB_LIST; echo --; fuPROTECTED; echo --\n'
                  'for d in disk4 rdisk4 /dev/rdisk4 disk4s1 disk0 disk7 disk9 sdb; do\n'
                  '  if fuUSB_FIND "$d" >/dev/null; then echo "$d yes"; else echo "$d no"; fi\n'
                  'done\n')
        result = self.source(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        sticks, protected, found = result.stdout.split("--\n")
        self.assertEqual(sticks, "/dev/disk4|32015679488|SanDisk Ultra · 32.0 GB · USB · /dev/disk4 · STICK\n"
                         "/dev/disk8|63864569856|Apple SDXC Reader · 63.9 GB · Secure Digital · /dev/disk8\n")
        self.assertEqual(protected, "/dev/disk0\n/dev/disk7\n")
        self.assertEqual(found, "disk4 yes\nrdisk4 yes\n/dev/rdisk4 yes\ndisk4s1 no\ndisk0 no\ndisk7 no\n"
                         "disk9 no\nsdb no\n")

    def test_writing_an_iso_to_a_stick(self):
        build = self.tree()
        stick = self.stick()
        iso = os.path.join(self.tmp, "isos", "tpot-master-debian-13.7.0-amd64.iso")
        data = bytes(range(256)) * 12288
        iso_with_sums(iso, data)
        result = self.run_build(build, "-w", iso, "-d", stick, "-y")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(self.head(stick, len(data)), data)
        self.assertIn("Read back: the same sha256 as the ISO", output)
        self.assertIn("Remove the stick", output)
        self.assertIn("Boot from it (BIOS or UEFI)", output)

    def test_a_stick_gets_only_an_iso_as_it_was_built_that_fits(self):
        build = self.tree()
        stick = self.stick(size=1024 * 1024)
        big = os.path.join(self.tmp, "big.iso")
        iso_with_sums(big, b"B" * 2 * 1024 * 1024)
        result = self.run_build(build, "-w", big, "-d", stick, "-y")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("does not fit", result.stdout + result.stderr)
        changed = os.path.join(self.tmp, "changed.iso")
        iso_with_sums(changed, b"C" * 4096)
        write(changed, b"D" * 4096)
        result = self.run_build(build, "-w", changed, "-d", stick, "-y")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("its .sha256 says", result.stdout + result.stderr)
        for args in (["-w", changed, "-d", "/dev/sda", "-y"], ["-w", changed]):
            self.assertEqual(self.run_build(build, *args).returncode, 2, args)
        self.assertEqual(self.head(stick, 2 * 1024 * 1024), b"\0" * 1024 * 1024)

    def test_reading_the_stick_back(self):
        stick = self.stick()
        iso = os.path.join(self.tmp, "a.iso")
        data = b"T-Pot" * 100000
        write(iso, data)
        with open(stick, "r+b") as handle:
            handle.write(data)
        check = f'fuSTICK_CHECK "{stick}" "{iso}" {len(data)}'
        self.assertEqual(self.source(check).returncode, 0)
        with open(stick, "r+b") as handle:
            handle.seek(1000)
            handle.write(b"X")
        result = self.source(check)
        self.assertEqual(result.returncode, 1)
        self.assertIn("the ISO has", result.stderr)
        self.assertEqual(self.source(f'fuSTICK_CHECK "{self.tmp}/gone" "{iso}" {len(data)}').returncode, 2)

    def test_the_checksums_of_the_isos_and_of_a_release(self):
        out = os.path.join(self.tmp, "release")
        names = [f"tpot-24.04.2-debian-13.7.0-{arch}.iso" for arch in ("amd64", "arm64")]
        for name in names:
            write(os.path.join(out, name), f"{name}\n" * 1000)
        built = f'myOUT="{out}"; myBUILT=({" ".join(names)})\n'
        result = self.source(built + 'for i in "${myBUILT[@]}"; do fuCHECKSUMS "${myOUT}/$i" || exit 1; done\n'
                             'fuWRITE_SUMS')
        self.assertEqual(result.returncode, 0, result.stderr)
        for bits, algorithm in ((256, hashlib.sha256), (512, hashlib.sha512)):
            lines = ""
            for name in names:
                line = f"{algorithm(base.read(os.path.join(out, name)).encode()).hexdigest()}  {name}\n"
                self.assertEqual(base.read(os.path.join(out, f"{name}.sha{bits}")), line)
                lines += line
            self.assertEqual(base.read(os.path.join(out, f"SHA{bits}SUMS")), lines)
        checker = ["sha256sum", "-c"] if shutil.which("sha256sum") else ["shasum", "-a", "256", "-c"]
        for name in ("SHA256SUMS", f"{names[0]}.sha256"):
            self.assertEqual(subprocess.run(checker + [name], cwd=out, capture_output=True).returncode, 0, name)
        # an ISO changed after its checksums: no SUMS file at all
        with open(os.path.join(out, names[1]), "a") as handle:
            handle.write("x")
        result = self.source(built + "fuWRITE_SUMS")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{names[1]}: its sha256 is not the one of its .sha256", result.stderr)
        self.assertFalse([name for name in os.listdir(out) if "SUMS" in name])

    def git(self, *args, cwd=None):
        environ = self.env(GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.org", GIT_COMMITTER_NAME="T",
                           GIT_COMMITTER_EMAIL="t@example.org")
        result = subprocess.run(["git", *args], cwd=cwd or self.tmp, capture_output=True, text=True, env=environ,
                                timeout=60)
        self.assertEqual(result.returncode, 0, (args, result.stderr))
        return result.stdout.strip()

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_a_release_only_from_a_clean_and_pushed_branch(self):
        remote = os.path.join(self.tmp, "remote.git")
        repo = os.path.join(self.tmp, "repo")
        self.git("init", "-q", "--bare", remote)
        self.git("init", "-q", repo)
        self.git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
        write(os.path.join(repo, "version"), "24.04.2\n")
        self.git("add", "version", cwd=repo)
        self.git("commit", "-qm", "release", cwd=repo)
        self.git("tag", "24.04.2", cwd=repo)
        self.git("remote", "add", "origin", remote, cwd=repo)
        self.git("push", "-q", "origin", "master", "24.04.2", cwd=repo)
        head = self.git("rev-parse", "HEAD", cwd=repo)

        def check(branch, url=remote):
            result = self.source(f'myREPO="{repo}" myREPO_URL="{url}" myBRANCH="{branch}"\n'
                                 'fuRELEASE_CHECK && echo "version ${myVERSION}"')
            return result.returncode, result.stdout + result.stderr

        code, out = check("master")
        self.assertEqual(code, 0, out)
        self.assertIn("version 24.04.2", out)
        self.git("checkout", "-qb", "dev", cwd=repo)
        self.git("push", "-q", "origin", "dev", cwd=repo)
        code, out = check("dev")
        self.assertEqual(code, 0, out)
        self.assertIn("a release usually master", out)
        self.git("checkout", "-q", "master", cwd=repo)
        for branch, url, text in (("24.04.2", remote, "is a tag"), ("nope", remote, "has no branch nope"),
                                  (head, remote, "is a commit"),
                                  ("master", os.path.join(self.tmp, "none.git"), "cannot be reached")):
            code, out = check(branch, url)
            self.assertEqual(code, 1, (branch, out))
            self.assertIn(text, out)
        self.assertIn("-b master", check("24.04.2")[1])
        write(os.path.join(repo, "new"), "x")
        code, out = check("master")
        self.assertEqual(code, 1, out)
        self.assertIn("The checkout has changes", out)
        self.git("add", "new", cwd=repo)
        self.git("commit", "-qm", "not pushed", cwd=repo)
        code, out = check("master")
        self.assertEqual(code, 1, out)
        self.assertIn(f"has master at {head[:7]}", out)

    def test_the_gpg_keys_that_sign(self):
        good = "A" * 40
        write(os.path.join(self.stubs, "keys"), "\n".join([
            "sec:u:255:22:AAAAAAAAAAAAAAAA:1700000000:::u:::scESC:::+:::ed25519:::0:",
            f"fpr:::::::::{good}:",
            f"grp:::::::::{'E' * 40}:",
            "uid:u::::1700000000::0123::T-Pot Release <tpot@example.org>::::::::::0:",
            "ssb:u:255:18:DDDDDDDDDDDDDDDD:1700000000::::::e:::+:::cv25519::",
            f"fpr:::::::::{'D' * 40}:",
            # expired, then one that only encrypts
            "sec:e:255:22:BBBBBBBBBBBBBBBB:1600000000:1650000000::u:::scSC:::+:::ed25519:::0:",
            f"fpr:::::::::{'B' * 40}:",
            "uid:e::::1600000000::0456::Expired <old@example.org>::::::::::0:",
            "sec:u:3072:1:CCCCCCCCCCCCCCCC:1700000000:::u:::eE:::+::::::0:",
            f"fpr:::::::::{'C' * 40}:",
            "uid:u::::1700000000::0789::Encrypt Only <enc@example.org>::::::::::0:",
        ]) + "\n")
        stub(self.bin, "gpg", f'cat "{self.stubs}/keys"\n')
        result = self.source('fuGPG_KEYS; mySIGN_KEY=tpot@example.org; fuSIGN_KEY && echo "fpr ${mySIGN_FPR}"')
        self.assertEqual(result.stdout, f"{good}|T-Pot Release <tpot@example.org>\nfpr {good}\n")

    def test_signing_the_sums(self):
        fpr, other = "A" * 40, "B" * 40
        stub(self.bin, "gpg", 'out=""; prev=""\n'
             'for a in "$@"; do [ "$prev" != "--output" ] || out="$a"; prev="$a"; done\n'
             'case " $* " in\n'
             '  *" --detach-sign "*) [ "${STUB_SIGN_RC:-0}" = 0 ] || exit "$STUB_SIGN_RC"; echo signature > "$out" ;;\n'
             '  *" --verify "*) printf "%b" "$STUB_STATUS" ;;\n'
             '  *) exit 1 ;;\n'
             'esac\n')
        sums = os.path.join(self.tmp, "SHA256SUMS")
        write(sums, "0  a.iso\n")
        sign = f'mySIGN_FPR="{fpr}"; fuSIGN "{sums}"'
        for status, sign_rc, code in ((f"[GNUPG:] VALIDSIG {'D' * 40} 2026-01-01 0 0 4 0 22 10 00 {fpr}\\n", 0, 0),
                                      (f"[GNUPG:] VALIDSIG {other} 2026-01-01 0 0 4 0 22 10 00 {other}\\n", 0, 1),
                                      ("", 2, 1)):
            result = self.source(sign, STUB_STATUS=status, STUB_SIGN_RC=str(sign_rc))
            self.assertEqual(result.returncode, code, (status, result.stdout, result.stderr))
            self.assertEqual(os.path.exists(f"{sums}.asc"), code == 0)

    def test_the_upload_takes_the_files_of_the_run(self):
        out = os.path.join(self.tmp, "release")
        names = [f"tpot-24.04.2-debian-13.7.0-{arch}.iso" for arch in ("amd64", "arm64")]
        files = [f"{name}{suffix}" for name in names for suffix in ("", ".sha256", ".sha512", ".buildinfo")]
        files += ["SHA256SUMS", "SHA512SUMS", "SHA256SUMS.asc", "SHA512SUMS.asc"]
        for name in files + ["tpot-24.04.1-debian-13.6.0-amd64.iso", "build.log"]:
            write(os.path.join(out, name), "x")
        stub(self.bin, "gh", f'printf "%s\\n" "$@" > "{self.stubs}/gh"\n')
        result = self.source(f'myOUT="{out}" myUPLOAD_TAG=24.04.2 myGH_REPO=telekom-security/tpotce; '
                             f'myBUILT=({" ".join(names)}); fuUPLOAD')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(base.read(os.path.join(self.stubs, "gh")).splitlines(),
                         ["release", "upload", "24.04.2", *[os.path.join(out, name) for name in files], "--clobber",
                          "--repo", "telekom-security/tpotce"])
        check = 'myREPO_URL="{}" myUPLOAD_TAG=24.04.2; fuUPLOAD_CHECK && echo "repo ${{myGH_REPO}}"'
        result = self.source(check.format("https://gitlab.com/x/tpotce"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is no repository there", result.stderr + result.stdout)
        stub(self.bin, "gh", "exit 1\n")
        result = self.source(check.format("https://github.com/telekom-security/tpotce"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("gh cannot see the release 24.04.2", result.stderr + result.stdout)
        stub(self.bin, "gh", "exit 0\n")
        result = self.source(check.format("https://github.com/telekom-security/tpotce"))
        self.assertIn("repo telekom-security/tpotce", result.stdout)

    def test_a_release_run_and_the_files_of_an_earlier_one(self):
        out = os.path.join(self.tmp, "release")
        run = ('fuHOW () { myHOW="here" myHOW_TEXT="Build here"; }\n'
               'fuRELEASE_CHECK () { myVERSION="24.04.2"; }\n'
               'fuSNAPSHOT () { :; }\n'
               'fuBUILD_ONE () {\n'
               '  local myISO="${myPREFIX}-debian-13.7.0-$1.iso"\n'
               '  echo "ISO $1" > "${myOUT}/${myISO}"\n'
               '  fuCHECKSUMS "${myOUT}/${myISO}" >/dev/null && echo "iso: ${myISO}" > "${myOUT}/${myISO}.buildinfo"\n'
               '  printf "%s\\n\\n" "${myISO}" > "${myOUT}/.last-$1"\n'
               '}\n'
               f'myOUT="{out}" myOUT_GIVEN=1 myCACHE="{self.tmp}/cache" myRELEASE=1 myARCH=both myARCHS=(amd64 arm64)\n'
               'myBRANCH=master myREPO_URL=https://github.com/telekom-security/tpotce\n')
        result = self.source(run + "fuRUN")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        names = [f"tpot-24.04.2-debian-13.7.0-{arch}.iso" for arch in ("amd64", "arm64")]
        self.assertEqual(sorted(os.listdir(out)), sorted(
            ["SHA256SUMS", "SHA512SUMS", "build.log"]
            + [f"{name}{suffix}" for name in names for suffix in ("", ".sha256", ".sha512", ".buildinfo")]))
        self.assertEqual(base.read(os.path.join(out, "SHA256SUMS")),
                         "".join(base.read(os.path.join(out, f"{name}.sha256")) for name in names))
        self.assertIn("SHA256SUMS and SHA512SUMS (not signed)", output)
        self.assertIn("gh release upload <tag> " + " ".join(
            [f"{name}{suffix}" for name in names for suffix in ("", ".sha256", ".sha512", ".buildinfo")]
            + ["SHA256SUMS", "SHA512SUMS"]) + " --clobber --repo telekom-security/tpotce", output)
        # an earlier release build there: without a terminal only with -y, which keeps other files
        write(os.path.join(out, "tpot-24.04.1-debian-13.6.0-amd64.iso"), "old")
        write(os.path.join(out, "notes.txt"), "mine")
        result = self.source(run + "fuRUN")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has the files of an earlier build", result.stdout + result.stderr)
        self.assertTrue(os.path.exists(os.path.join(out, "tpot-24.04.1-debian-13.6.0-amd64.iso")))
        result = self.source(run + "myYES=1; fuRUN")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "tpot-24.04.1-debian-13.6.0-amd64.iso")))
        self.assertEqual(base.read(os.path.join(out, "notes.txt")), "mine")
        self.assertTrue(os.path.exists(os.path.join(out, names[1])))

    def test_the_cache_of_the_netinst_follows_debian(self):
        web = os.path.join(self.tmp, "web")
        cache = os.path.join(self.tmp, "cache")
        work = os.path.join(self.tmp, "work")
        fetched = os.path.join(self.tmp, "fetched")
        name = "debian-13.8.0-amd64-netinst.iso"
        new = b"Debian 13.8.0 netinst\n" * 1000
        write(os.path.join(web, name), new)
        write(os.path.join(web, "SHA512SUMS"),
              f"{hashlib.sha512(new).hexdigest()}  {name}\n{'0' * 128}  debian-13.8.0-amd64-DVD-1.iso\n")
        write(os.path.join(web, "key-DA87E80D6294BE9B.txt"), "key\n")
        write(os.path.join(cache, "debian-13.7.0-amd64-netinst.iso"), "old")
        write(os.path.join(cache, "debian-13.7.0-amd64-netinst.iso.part"), "cut off")
        write(os.path.join(cache, "debian-13.7.0-arm64-netinst.iso"), "arm64")
        os.makedirs(work)
        stub(self.bin, "curl", 'while [ $# -gt 1 ]; do [ "$1" != -o ] || out=$2; shift; done\n'
             f'echo "${{1##*/}}" >> "{fetched}"\ncp "{web}/${{1##*/}}" "$out"\n')
        # the signature of SHA512SUMS is checked by fuSUMS (test_the_netinst_only_with_the_signature_...)
        script = (f'myWORK="{work}" myCACHE="{cache}" myINPUT=""\n'
                  'fuSUMS () { mkdir -p "$2" && fuFETCH "$1/SHA512SUMS" "$2/SHA512SUMS"; }\n'
                  'fuNETINST amd64 && echo "note $(sed -n 5p "${myWORK}/netinst")"')

        def netinst():
            if os.path.exists(fetched):
                os.remove(fetched)
            result = self.source(script)
            got = base.read(fetched).split() if os.path.exists(fetched) else []
            return result.returncode, result.stdout + result.stderr, got

        code, out, got = netinst()
        self.assertEqual(code, 0, out)
        self.assertIn(f"note New Debian netinst {name}: downloaded, replaces debian-13.7.0-amd64-netinst.iso in the "
                      "cache", out)
        self.assertIn(name, got)
        self.assertEqual(sorted(os.listdir(cache)), ["debian-13.7.0-arm64-netinst.iso", name])
        code, out, got = netinst()
        self.assertIn(f"note Debian netinst {name}: up to date in the cache", out)
        self.assertNotIn(name, got)
        write(os.path.join(cache, name), "broken")
        code, out, got = netinst()
        self.assertIn("the cached one did not match Debian's SHA512SUMS, downloaded again", out)
        self.assertEqual(self.head(os.path.join(cache, name), len(new) + 1), new)
        # a download cut off goes on
        os.remove(os.path.join(cache, name))
        write(os.path.join(cache, f"{name}.part"), new[:5000])
        code, out, got = netinst()
        self.assertEqual(code, 0, out)
        self.assertIn("going on with", out)
        self.assertEqual(sorted(os.listdir(cache)), ["debian-13.7.0-arm64-netinst.iso", name])
        # Debian's file does not match its SHA512SUMS: nothing stays
        os.remove(os.path.join(cache, name))
        write(os.path.join(web, name), b"changed")
        code, out, got = netinst()
        self.assertEqual(code, 1, out)
        self.assertEqual(sorted(os.listdir(cache)), ["debian-13.7.0-arm64-netinst.iso"])

    def test_gum_for_macos_only_with_its_checksum(self):
        self.assertEqual(re.search(r'^myGUM_DARWIN_VERSION="([^"]+)"$', base.read("installer/iso/build.sh"), re.M)[1],
                         re.search(r'^myUI_GUM_VERSION="([^"]+)"$', base.read("installer/lib/ui.sh"), re.M)[1])
        archive = io.BytesIO()
        body = b'#!/bin/sh\necho "gum version v2.0.2"\n'
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            info = tarfile.TarInfo("gum_2.0.2_Darwin_arm64/gum")
            info.size, info.mode = len(body), 0o755
            tar.addfile(info, io.BytesIO(body))
        tgz = os.path.join(self.tmp, "gum.tgz")
        write(tgz, archive.getvalue())
        stub(self.bin, "uname", 'case "$1" in -m) echo arm64 ;; *) echo Darwin ;; esac\n')
        stub(self.bin, "curl", f'while [ $# -gt 1 ]; do [ "$1" != -o ] || out=$2; shift; done\ncp "{tgz}" "$out"\n')
        gum = os.path.join(self.tmp, ".local", "share", "tpotce", "bin", "gum")
        for pinned, installed in (("", False), (hashlib.sha256(archive.getvalue()).hexdigest(), True)):
            script = f'source "{BUILD}"\n' + (f'myGUM_DARWIN_SHA256_arm64="{pinned}"\n' if pinned else "") + "fuGUM_MACOS"
            code, out, _piped = ptyhelper.run([BASH, "-c", script], self.env(TPOT_GUM="on"), cwd=self.tmp, timeout=30)
            self.assertEqual(code, 0, out)
            self.assertEqual(os.access(gum, os.X_OK), installed, out)
        self.assertFalse([name for name in os.listdir(self.tmp) if name.startswith("tpot-gum.")])

    def test_the_command_line_of_a_choice(self):
        result = self.source(
            'myDEF_BRANCH=master myDEF_REPO_URL=https://github.com/telekom-security/tpotce\n'
            'myARCH=amd64 myBRANCH=dev myREPO_URL=https://github.com/x/y myDOCKER=1; fuCOMMAND_LINE; echo\n'
            'myBRANCH=master myREPO_URL=https://github.com/telekom-security/tpotce myDOCKER="" myARCH=both myRELEASE=1 '
            'mySIGN_FPR=ABCD myUPLOAD_TAG=24.04.2; fuCOMMAND_LINE; echo\n'
            'fuCOMMAND_LINE write "/my isos/a.iso" /dev/sdb; echo\n')
        lines = [shlex.split(line) for line in result.stdout.splitlines()]
        for line in lines:
            self.assertTrue(line[0].endswith("/installer/iso/build.sh"), line)
        self.assertEqual([line[1:] for line in lines], [
            ["-a", "amd64", "-b", "dev", "-r", "https://github.com/x/y", "-D"],
            ["-a", "both", "-R", "-s", "ABCD", "-U", "24.04.2"],
            ["-w", "/my isos/a.iso", "-d", "/dev/sdb"]])

    def menu(self, build, answers, git="exit 128\n"):
        """The menu of build.sh at a terminal (plain text), the answers typed after their questions; git
        (default) and Debian cannot be reached, Docker answers."""
        stub(self.bin, "git", git)
        stub(self.bin, "curl", "exit 7\n")
        stub(self.bin, "docker", "exit 0\n")
        code, out, _piped = ptyhelper.run([BASH, build], self.env(), cwd=self.tmp, size=(220, 50), answers=answers,
                                          timeout=60)
        return code, out.decode("utf-8", "replace").replace("\r\n", "\n")

    # git of a repository with the branches zeta, master, -bad (no name start.sh takes), feature/x and dev,
    # the tag 24.04.2; no checkout here (rev-parse fails)
    GIT_BRANCHES = ('case "$*" in\n'
                    '  *"ls-remote --heads"*) printf "a\\trefs/heads/%s\\n" zeta master -bad feature/x dev ;;\n'
                    '  *"ls-remote --exit-code"*)\n'
                    '    for b in feature/x dev master; do\n'
                    '      case "$* " in *"refs/heads/$b "*) printf "a\\trefs/heads/%s\\n" "$b"; exit 0 ;; esac\n'
                    '    done\n'
                    '    case "$*" in *refs/tags/24.04.2*) printf "b\\trefs/tags/24.04.2\\n"; exit 0 ;; esac\n'
                    '    exit 2 ;;\n'
                    '  *) exit 128 ;;\n'
                    'esac\n')

    def test_the_menu_lists_the_branches_of_the_repository(self):
        build = self.tree()
        code, out = self.menu(build, [("What do you want to do?", b"1\n"), ("For which machines?", b"1\n"),
                                      ("Where does the ISO install T-Pot from?", b"3\n"), ("Build now?", b"2\n"),
                                      ("What do you want to do?", b"4\n")], git=self.GIT_BRANCHES)
        self.assertEqual(code, 0, out)
        menu = out[out.index("The branches of https://github.com/telekom-security/tpotce:"):]
        for number, label in enumerate(("master (the release)", "dev (development, the next release)", "feature/x",
                                        "zeta", "A tag or a commit (update.sh cannot update it)",
                                        "Another repository (now https://github.com/telekom-security/tpotce)",
                                        "Back"), 1):
            self.assertIn(f"###   {number}) {label}\n", menu)
        self.assertNotIn("-bad", menu)
        self.assertIn("https://github.com/telekom-security/tpotce has feature/x (a)", out)
        self.assertIn(f"The same without the menu: {build} -a amd64 -b feature/x\n", out)

    @unittest.skipUnless(shutil.which("gum"), "no gum")
    def test_the_branch_list_can_be_filtered_with_real_gum(self):
        stub(self.bin, "git", self.GIT_BRANCHES)
        script = (f'source "{BUILD}"\nfuUI_INIT\nmyUI_GUM={shlex.quote(shutil.which("gum") or "")}\n'
                  'myBRANCH=dev myREPO_URL=https://github.com/telekom-security/tpotce\n'
                  'fuMENU_SOURCE || exit $?\nprintf "SOURCE=%s|%s\\n" "$myBRANCH" "$myREPO_URL"\n')
        for keys, branch in ((b"\r", "dev"), (b"feature/x\r", "feature/x")):
            with self.subTest(branch=branch):
                code, out, _piped = ptyhelper.run(
                    [BASH, "-c", script], self.env(TERM="xterm-256color"), cwd=self.tmp,
                    size=(180, 40), keys=keys, timeout=15)
                self.assertEqual(code, 0, out)
                self.assertIn(f"SOURCE={branch}|https://github.com/telekom-security/tpotce".encode(), out)

    def test_the_menu_takes_a_tag_with_a_warning(self):
        build = self.tree()
        code, out = self.menu(build, [("What do you want to do?", b"1\n"), ("For which machines?", b"1\n"),
                                      ("Where does the ISO install T-Pot from?", b"5\n"),
                                      ("Tag or commit", b"24.04.2\n"), ("Build now?", b"2\n"),
                                      ("What do you want to do?", b"4\n")], git=self.GIT_BRANCHES)
        self.assertEqual(code, 0, out)
        self.assertIn("A T-Pot of a tag or a commit is not on a branch, update.sh cannot update it (later on a "
                      "branch again: ./update.sh -y -b master).", out)
        summary = out[out.index("Ready to build"):]
        self.assertIn("[WARNING] - 24.04.2 is a tag of https://github.com/telekom-security/tpotce (b): the ISO "
                      "installs it, update.sh cannot update it (later on a branch again: ./update.sh -y -b master)",
                      summary)
        self.assertIn(f"The same without the menu: {build} -a amd64 -b 24.04.2\n", out)

    def test_the_menu_lists_the_branches_of_another_repository(self):
        build = self.tree()
        code, out = self.menu(build, [("What do you want to do?", b"1\n"), ("For which machines?", b"1\n"),
                                      ("Where does the ISO install T-Pot from?", b"6\n"),
                                      ("Repository URL", b"https://github.com/me/tpotce.git\n"),
                                      ("The branches of https://github.com/me/tpotce:", b"2\n"),
                                      ("Build now?", b"2\n"), ("What do you want to do?", b"4\n")],
                              git=self.GIT_BRANCHES)
        self.assertEqual(code, 0, out)
        self.assertIn(f"The same without the menu: {build} -a amd64 -b dev -r https://github.com/me/tpotce\n", out)

    def tpot_repo(self, remote, repo):
        """A bare remote and a checkout of a T-Pot (install.sh, version, the requirements of the Manager,
        ui.sh) with master, dev and the tag 24.04.2 pushed; the commit of master."""
        self.git("init", "-q", "--bare", remote)
        self.git("init", "-q", repo)
        self.git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
        for name, text in (("install.sh", "#!/bin/bash\n"), ("version", "24.04.2\n"),
                           ("tpotctl/requirements.txt", "rich==1\n"), ("installer/lib/ui.sh", 'myUI_GUM_VERSION="2.0.2"\n')):
            write(os.path.join(repo, name), text)
        self.git("add", "-A", cwd=repo)
        self.git("commit", "-qm", "release", cwd=repo)
        self.git("tag", "-a", "-m", "annotated", "24.04.2", cwd=repo)
        self.git("remote", "add", "origin", remote, cwd=repo)
        self.git("branch", "dev", cwd=repo)
        self.git("push", "-q", "origin", "--all", cwd=repo)
        self.git("push", "-q", "origin", "--tags", cwd=repo)
        return self.git("rev-parse", "HEAD", cwd=repo)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_the_t_pot_the_iso_carries(self):
        """fuSNAPSHOT_FROM / fuSNAPSHOT_GIT: the checkout for its own branch and origin (its last commit,
        pushed or not) and for a commit it has, else the repository; one commit as git clone leaves it."""
        remote = os.path.join(self.tmp, "remote.git")
        repo = os.path.join(self.tmp, "repo")
        released = self.tpot_repo(remote, repo)
        # a commit of this checkout that is not pushed, one of the repository that is not here
        write(os.path.join(repo, "version"), "24.04.3-dev\n")
        self.git("commit", "-qam", "local", cwd=repo)
        local = self.git("rev-parse", "HEAD", cwd=repo)
        other = os.path.join(self.tmp, "other")
        self.git("clone", "-q", "-b", "master", remote, other)
        write(os.path.join(other, "version"), "24.04.3-remote\n")
        self.git("commit", "-qam", "remote", cwd=other)
        self.git("push", "-q", "origin", "HEAD:refs/heads/feature", cwd=other)
        remote_only = self.git("rev-parse", "HEAD", cwd=other)
        dev = self.git("rev-parse", "dev", cwd=repo)
        cases = ((("master", remote), "checkout", local, "branch", "24.04.3-dev"),
                 (("dev", remote), "repository", dev, "branch", "24.04.2"),
                 (("master", "https://github.com/me/tpotce"), "repository", None, None, None),
                 (("24.04.2", remote), "repository", released, "tag", "24.04.2"),
                 ((released, remote), "checkout", released, "commit", "24.04.2"),
                 ((remote_only, remote), "repository", remote_only, "commit", "24.04.3-remote"))
        for (branch, url), source, commit, kind, version in cases:
            with self.subTest(branch=branch, url=url):
                snap = os.path.join(self.tmp, "snap")
                result = self.source(f'myREPO="{repo}" myDEF_BRANCH=master myDEF_REPO_URL="{remote}"\n'
                                     f'myBRANCH="{branch}" myREPO_URL="{url}"\n'
                                     'myFROM=$(fuSNAPSHOT_FROM); echo "from=${myFROM}"\n'
                                     f'[ "${{myFROM}}" = repository ] && [ "{url}" != "{remote}" ] && exit 0\n'
                                     f'fuSNAPSHOT_GIT "{snap}" "${{myFROM}}"')
                out = result.stdout + result.stderr
                self.assertIn(f"from={source}\n", out)
                if commit is None:
                    continue
                self.assertEqual(result.returncode, 0, out)
                self.assertIn(f"({kind} {commit}, from the {source})", out)
                head = self.git("rev-parse", "HEAD", cwd=snap)
                self.assertEqual(head, commit)
                self.assertEqual(base.read(os.path.join(snap, "commit")), commit + "\n")
                for name, text in (("version", version + "\n"), ("requirements.txt", "rich==1\n"),
                                   ("ui.sh", 'myUI_GUM_VERSION="2.0.2"\n'), ("install.sh", "#!/bin/bash\n")):
                    self.assertEqual(base.read(os.path.join(snap, name)), text, name)
                git = os.path.join(snap, ".git")
                self.assertTrue(os.path.isfile(os.path.join(git, "shallow")), "one commit")
                for name in ("logs", "FETCH_HEAD", "hooks", "description"):
                    self.assertFalse(os.path.exists(os.path.join(git, name)), name)
                config = base.read(os.path.join(git, "config"))
                self.assertNotIn("ignorecase", config)
                self.assertNotIn("precomposeunicode", config)
                self.assertEqual(self.git("config", "remote.origin.url", cwd=snap), url)
                symbolic = subprocess.run(["git", "-C", snap, "symbolic-ref", "-q", "HEAD"], capture_output=True,
                                          text=True).stdout.strip()
                if kind == "branch":
                    self.assertEqual(symbolic, f"refs/heads/{branch}")
                    self.assertEqual(self.git("rev-parse", "@{upstream}", cwd=snap), commit)
                    self.assertEqual(self.git("config", f"branch.{branch}.merge", cwd=snap), f"refs/heads/{branch}")
                else:
                    self.assertEqual(symbolic, "", "a tag or a commit: a detached HEAD")
                # what prepare.sh does with it (only the .git goes into the ISO): that commit, nothing changed
                self.git("reset", "-q", "--hard", cwd=snap)
                self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no", cwd=snap), "")
                shutil.rmtree(snap)
        # a branch the repository does not have, a T-Pot without the T-Pot Manager
        result = self.source(f'myREPO="{repo}" myDEF_BRANCH=master myDEF_REPO_URL="{remote}" myBRANCH=nope '
                             f'myREPO_URL="{remote}"\nfuSNAPSHOT_GIT "{self.tmp}/snap" repository')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{remote} has no branch or tag nope, or cannot be reached", result.stderr)
        self.git("rm", "-q", "tpotctl/requirements.txt", cwd=other)
        self.git("commit", "-qm", "old", cwd=other)
        self.git("push", "-q", "origin", "HEAD:refs/heads/old", cwd=other)
        result = self.source(f'myREPO="{repo}" myBRANCH=old myREPO_URL="{remote}"\n'
                             f'fuSNAPSHOT_GIT "{self.tmp}/snap" repository')
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no tpotctl/requirements.txt: the ISO needs a T-Pot with the T-Pot Manager", result.stderr)

    def test_the_t_pot_of_the_iso_needs_git(self):
        """fuSNAPSHOT: without git the build stops before anything is downloaded, with the hint."""
        stub(self.bin, "git", "exit 127\n")
        result = self.source('command () { [ "$2" != git ] && builtin command "$@"; }\n'
                             f'myOUT="{self.tmp}" myLOG="{self.tmp}/log" fuSNAPSHOT; echo "rc=$?"')
        out = result.stdout + result.stderr
        self.assertIn("rc=1", out)
        self.assertIn("git is missing here", out)
        self.assertIn("Debian: sudo apt-get install git; macOS: xcode-select --install", out)

    def test_gum_for_the_iso(self):
        """fuGUM_LINUX: the gum release the ui.sh of the T-Pot of the ISO pins for that machine, from
        the cache or downloaded, its sha256 checked."""
        gum = os.path.join(self.stubs, "gum.tar.gz")
        write(gum, b"gum release\n")
        sha = hashlib.sha256(b"gum release\n").hexdigest()
        snap = os.path.join(self.tmp, "snap")
        write(os.path.join(snap, "ui.sh"), f'myUI_GUM_VERSION="2.0.2"\nmyUI_GUM_SHA256_x86_64="{sha}"\n'
                                           f'myUI_GUM_SHA256_arm64="{sha}"\n')
        stub(self.bin, "curl", 'o=""; u=""\nwhile [ $# -gt 0 ]; do case "$1" in -o) o="$2"; shift ;; *) u="$1" ;; esac; '
                               'shift; done\necho "$u" >> "$STUBS/curl.calls"\ncp "$STUBS/gum.tar.gz" "$o"\n')
        run = (f'myCACHE="{self.tmp}/cache" mySNAPSHOT="{snap}"\nmkdir -p "$myCACHE" "{self.tmp}/out"\n'
               f'fuGUM_LINUX "$ARCH" "{self.tmp}/out"; echo "rc=$?"')
        calls = os.path.join(self.stubs, "curl.calls")
        for arch, machine in (("amd64", "x86_64"), ("arm64", "arm64")):
            with self.subTest(arch=arch):
                name = f"gum_2.0.2_Linux_{machine}.tar.gz"
                for _again in range(2):
                    result = self.source(run, ARCH=arch, STUBS=self.stubs)
                    self.assertIn("rc=0", result.stdout, result.stderr)
                    self.assertIn(f"{name}: sha256 matches ui.sh", result.stdout)
                self.assertEqual(base.read(calls).splitlines(),
                                 [f"https://github.com/charmbracelet/gum/releases/download/v2.0.2/{name}"],
                                 "downloaded once, then from the cache")
                self.assertEqual(base.read(os.path.join(self.tmp, "out", name)), "gum release\n")
                os.remove(calls)
        write(gum, b"another gum\n")
        os.remove(os.path.join(self.tmp, "cache", "gum_2.0.2_Linux_x86_64.tar.gz"))
        result = self.source(run, ARCH="amd64", STUBS=self.stubs)
        self.assertIn("rc=1", result.stdout)
        self.assertIn(f"its sha256 is not the one of ui.sh ({sha})", result.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "cache"))), ["gum_2.0.2_Linux_arm64.tar.gz"])
        write(os.path.join(snap, "ui.sh"), 'myUI_GUM_VERSION="2.0.2"\n')
        result = self.source(run, ARCH="amd64", STUBS=self.stubs)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("pins no gum for x86_64", result.stderr)

    def test_the_wheels_for_the_iso(self):
        """fuWHEELS: the requirements of the T-Pot Manager for the python3 of the Debian of the ISO (its
        markers decided by isotree.py), pip download for that python and machine, every wheel there."""
        snap = os.path.join(self.tmp, "snap")
        hashes = " \\\n    --hash=sha256:" + "1" * 64 + "\n"
        write(os.path.join(snap, "requirements.txt"),
              "rich==14.0.0" + hashes + "colorama==0.4.6 ; sys_platform == 'win32'" + hashes
              + "textual==3.2.0 ; python_version >= '3.9'" + hashes)
        stub(self.bin, "python3",
             'if [ "$1 $2" = "-m pip" ]; then\n'
             '  echo "$*" >> "$STUBS/pip.calls"\n  d=""; r=""\n'
             '  while [ $# -gt 0 ]; do case "$1" in --dest) d="$2"; shift ;; -r) r="$2"; shift ;; esac; shift; done\n'
             '  [ -z "${PIP_FEWER:-}" ] || exit 0\n'
             '  sed -n "s/^\\([a-z]*\\)==\\([0-9.]*\\).*/\\1-\\2-py3-none-any.whl/p" "$r" | while read -r w; do : > "$d/$w"; done\n'
             '  exit 0\nfi\n'
             f'exec "{sys.executable}" "$@"\n')
        run = (f'myCACHE="{self.tmp}/cache" myWORK="{self.tmp}/work" mySNAPSHOT="{snap}"\nmkdir -p "$myWORK"\n'
               f'fuWHEELS "$ARCH" "{self.tmp}/wheels-$ARCH"; echo "rc=$?"')
        for arch, machine in (("amd64", "x86_64"), ("arm64", "aarch64")):
            with self.subTest(arch=arch):
                result = self.source(run, ARCH=arch, STUBS=self.stubs)
                self.assertIn("rc=0", result.stdout, result.stderr)
                self.assertIn(f"2 wheels for python 3.13 on {machine}", result.stdout)
                self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, f"wheels-{arch}"))),
                                 ["rich-14.0.0-py3-none-any.whl", "textual-3.2.0-py3-none-any.whl"])
                call = base.read(os.path.join(self.stubs, "pip.calls")).splitlines()[-1]
                for part in ("download", "--only-binary :all: --no-deps --require-hashes", "--implementation cp",
                             "--python-version 3.13 --abi cp313", f"--platform manylinux_2_41_{machine} ",
                             f"--platform manylinux_2_17_{machine} --platform manylinux2014_{machine} ",
                             f"--cache-dir {self.tmp}/cache/pip"):
                    self.assertIn(part, call)
                self.assertNotIn("manylinux_2_16", call)
                self.assertNotIn("colorama", base.read(os.path.join(self.tmp, "work", "requirements.txt")))
        result = self.source(run, ARCH="amd64", STUBS=self.stubs, PIP_FEWER="1")
        self.assertIn("rc=1", result.stdout)
        self.assertIn("0 wheels for 2 requirements of python 3.13 on x86_64", result.stderr)
        result = self.source('myDEBIAN_MAJOR=99\n' + run, ARCH="amd64", STUBS=self.stubs)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("no python3 known for Debian 99 (fuDEBIAN_PYTHON)", result.stderr)

    def test_the_python_of_the_debian_of_the_iso(self):
        result = self.source('fuDEBIAN_PYTHON "${myDEBIAN_MAJOR}"')
        self.assertEqual(result.returncode, 0, "fuDEBIAN_PYTHON needs a line for myDEBIAN_MAJOR")
        self.assertRegex(result.stdout, r"^3\.\d+ 2\.\d+\n$")

    def test_a_build_here_needs_pip(self):
        for name in ("xorriso", "gpg", "curl"):
            stub(self.bin, name, "exit 0\n")
        stub(self.bin, "python3", '[ "$1 $2" != "-m pip" ] || exit 1\n')
        result = self.source("fuTOOLS_MISSING")
        self.assertEqual(result.stdout, "python3-pip\n")
        stub(self.bin, "python3", "exit 0\n")
        result = self.source("fuTOOLS_MISSING")
        self.assertEqual(result.stdout, "\n")

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_what_the_repository_has_of_the_branch(self):
        """fuREMOTE: a branch is ok, a tag (annotated or not) and a commit install but cannot be updated,
        both warnings now; fuBRANCHES: master and dev first, names start.sh does not take left out."""
        remote = os.path.join(self.tmp, "remote.git")
        repo = os.path.join(self.tmp, "repo")
        self.git("init", "-q", "--bare", remote)
        self.git("init", "-q", repo)
        self.git("symbolic-ref", "HEAD", "refs/heads/master", cwd=repo)
        write(os.path.join(repo, "version"), "24.04.2\n")
        self.git("add", "version", cwd=repo)
        self.git("commit", "-qm", "release", cwd=repo)
        self.git("tag", "light", cwd=repo)
        self.git("tag", "-a", "-m", "annotated", "24.04.2", cwd=repo)
        for branch in ("zeta", "dev", "alpha"):
            self.git("branch", branch, cwd=repo)
        self.git("push", "-q", remote, "--all", cwd=repo)
        self.git("push", "-q", remote, "--tags", cwd=repo)
        head = self.git("rev-parse", "HEAD", cwd=repo)
        cases = (("master", remote, "ok", f"{remote} has master ({head[:7]})"),
                 ("24.04.2", remote, "tag", f"24.04.2 is a tag of {remote} ({head[:7]}): the ISO installs it, "
                                            "update.sh cannot update it (later on a branch again: ./update.sh "
                                            "-y -b master)"),
                 ("light", remote, "tag", f"light is a tag of {remote} ({head[:7]})"),
                 (head, remote, "commit", f"{head[:7]} is a commit: the ISO installs it, update.sh cannot "
                                          "update it"),
                 ("nope", remote, "missing", f"{remote} has no branch or tag nope: update.sh of an installed T-Pot "
                                             "could not follow it"),
                 ("master", os.path.join(self.tmp, "none.git"), "unreachable", "cannot be reached now"))
        for branch, url, state, text in cases:
            with self.subTest(branch=branch, url=url):
                result = self.source(f'myREPO="{self.tmp}" myREPO_URL="{url}" myBRANCH="{branch}"\n'
                                     'fuCHECK_REMOTE; echo "state=${myREMOTE} text=${myREMOTE_TEXT}"')
                out = result.stdout + result.stderr
                self.assertIn(f"state={state} ", out)
                self.assertIn(text, out)
                self.assertEqual("[WARNING]" in out, state != "ok", out)
        listing = os.path.join(self.tmp, "branches")
        self.git("push", "-q", remote, "master:refs/heads/-bad", cwd=repo)
        result = self.source(f'fuBRANCHES "{remote}" "{listing}"; echo "rc=$?"')
        self.assertIn("rc=0", result.stdout, result.stderr)
        with open(listing, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "master\ndev\nalpha\nzeta\n")
        result = self.source(f'fuBRANCHES "{os.path.join(self.tmp, "none.git")}" "{listing}"; echo "rc=$?"')
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(os.path.getsize(listing), 0)

    def test_the_menu_shows_a_build_with_its_command_line(self):
        build = self.tree()
        self.stick()
        code, out = self.menu(build, [("What do you want to do?", b"1\n"), ("For which machines?", b"1\n"),
                                      ("Where does the ISO install T-Pot from?", b"1\n"), ("Build now?", b"2\n"),
                                      ("What do you want to do?", b"4\n")])
        self.assertEqual(code, 0, out)
        self.assertIn("Ready to build", out)
        self.assertIn("Debian cannot be reached now", out)
        self.assertIn(f"The same without the menu: {build} -a amd64\n", out)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(build), "out")))

    def test_the_menu_writes_an_iso_to_a_stick(self):
        build = self.tree()
        stick = self.stick()
        iso = os.path.join(self.tmp, "x.iso")
        data = bytes(range(256)) * 4096
        iso_with_sums(iso, data)
        code, out = self.menu(build, [("What do you want to do?", b"2\n"), ("Which ISO?", b"1\n"),
                                      ("The path of the ISO", b"~/x.iso\n"), ("Which USB stick?", b"1\n"),
                                      ("and write the ISO? (y/N)", b"y\n")])
        self.assertEqual(code, 0, out)
        self.assertIn(f"Test Stick · 8 MB · USB · {stick}", out)
        self.assertIn(f"The same without the menu: {build} -w {iso} -d {stick}", out)
        self.assertIn("Read back: the same sha256 as the ISO", out)
        self.assertEqual(self.head(stick, len(data)), data)

    def test_the_menu_refuses_a_release_outside_a_git_checkout(self):
        build = self.tree()
        self.stick()
        code, out = self.menu(build, [("What do you want to do?", b"3\n"), ("What do you want to do?", b"4\n")])
        self.assertEqual(code, 0, out)
        self.assertIn("The ISOs of a release are built from a git checkout", out)

    def test_the_maintainer_menu_selects_signing_and_upload(self):
        build = self.tree()
        fpr = "A" * 40
        stub(self.bin, "gpg",
             f"printf '%s\\n' 'sec:u:255:22:AAAAAAAAAAAAAAAA:1700000000:::u:::scESC:' "
             f"'fpr:::::::::{fpr}:' 'uid:u::::::::T-Pot Release <tpot@example.org>:'\n")
        stub(self.bin, "gh", f'printf "%s\\n" "$@" >> "{self.stubs}/gh"\n')
        script = (f'source "{build}"\n'
                  'myBRANCH=master myDEF_BRANCH=master myREPO_URL="$myUPSTREAM" myDEF_REPO_URL="$myUPSTREAM"\n'
                  'fuRELEASE_CHECK () { myVERSION=24.04.2; }\n'
                  'fuHOW () { myHOW=here myHOW_TEXT="Build here"; }\n'
                  'fuPEEK () { printf "ok\\nTest repository\\n" > "$1"; }\n'
                  'fuMENU_RELEASE; echo "menu_rc=$?"\nfuCOMMAND_LINE; echo\n')
        cases = [(True, True, False), (False, False, False)]
        gum = shutil.which("gum")
        if gum:
            cases.extend([(True, True, True), (False, False, True)])
        for sign, upload, real_gum in cases:
            with self.subTest(sign=sign, upload=upload, gum=real_gum):
                run_script = script
                if real_gum:
                    run_script = script.replace('fuMENU_RELEASE;',
                                                f'fuUI_INIT; myUI_GUM={shlex.quote(gum)}; fuMENU_RELEASE;')
                enter = b"\r" if real_gum else b"\n"
                answers = [("For which machines?", enter),
                           ("Sign SHA256SUMS", (enter if sign else b"\x1b[B\r") if real_gum
                            else (b"1\n" if sign else b"2\n")),
                           ("Upload the files to a GitHub release?",
                            (b"y" if upload else b"n") + (b"" if real_gum else b"\n"))]
                if upload:
                    answers.append(("The tag of the release", enter))
                answers.append(("Build now?", b"\x1b[B\r" if real_gum else b"2\n"))
                code, out, _piped = ptyhelper.run([BASH, "-c", run_script],
                                                self.env(TERM="xterm-256color", NO_COLOR="" if real_gum else "1"),
                                                cwd=self.tmp,
                                                size=(220, 50), answers=answers, timeout=30)
                text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.decode("utf-8", "replace"))
                self.assertEqual(code, 0, text)
                self.assertIn("menu_rc=1", text)
                self.assertIn("Ready to build the ISOs of release 24.04.2", text)
                command = f"{build} -a both -R" + (f" -s {fpr} -U 24.04.2" if sign else "")
                self.assertIn(f"The same without the menu: {command}", text)
                self.assertIn(f"Signed by {fpr}" if sign else "Not signed", text)
                self.assertIn("Uploaded to https://github.com/telekom-security/tpotce/releases/tag/24.04.2"
                              if upload else "Not uploaded", text)
                self.assertFalse(os.path.exists(os.path.join(os.path.dirname(build), "out")))
        self.assertEqual(base.read(os.path.join(self.stubs, "gh")).splitlines(),
                         ["release", "view", "24.04.2", "--repo", "telekom-security/tpotce"] * (2 if gum else 1))


if __name__ == "__main__":
    unittest.main()
