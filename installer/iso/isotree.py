"""The boot menus of the T-Pot ISO, for installer/iso/build.sh. Standard library only.

    python3 installer/iso/isotree.py patch --arch ARCH --orig DIR --out DIR
        the boot files of a Debian netinst (extracted to --orig at their paths in the ISO) with the
        entries "Install T-Pot" (the default) and "Install T-Pot (graphical installer)" in front of
        the ones of Debian, which stay: GRUB (UEFI, both architectures) and ISOLINUX (BIOS, amd64,
        its timeout starts Install T-Pot), the T-Pot logo as the background. Writes the changed and
        new files to --out at their paths in the ISO and prints those paths.
    python3 installer/iso/isotree.py md5sum --orig FILE --out DIR
        md5sum.txt of the ISO (--orig) with the files of --out: their lines updated or added, as the
        installer's check of the medium reads it. Writes DIR/md5sum.txt.
    python3 installer/iso/isotree.py splash FILE
        the background alone (a preview).

Every change looks for the exact text of the Debian netinst it was written for: a newer one that
differs stops the build with what was not found, never a half patched menu. The kernels, the initrds,
the boot loaders and their signatures (Secure Boot) stay untouched.
"""

import argparse
import hashlib
import os
import re
import struct
import sys
import zlib
from typing import Dict, List, Optional, Sequence, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tpotctl import splash_art  # noqa: E402  (the logo, the one source of its pixels)

PRESEED = "preseed/file=/cdrom/tpot/preseed.cfg"
MAGENTA = (0xE2, 0x00, 0x74)
TITLE = "Install T-Pot"
TITLE_GUI = "Install T-Pot (graphical installer)"
BIOS_TITLE = "T-Pot installer on Debian (BIOS mode)"
# ISOLINUX counts down (# the seconds) the timeout of Debian's spkgtk.cfg, 30 seconds
BIOS_AUTOBOOT = "Press a key, otherwise Install T-Pot starts in # second{,s}..."
ISOLINUX_FILES = ("menu.cfg", "txt.cfg", "gtk.cfg", "stdmenu.cfg", "spkgtk.cfg")
# the background: ISOLINUX shows it at 640 x 480, GRUB stretches it to 800 x 600; the logo stays above
# the menus (ISOLINUX: menu vshift 8, GRUB: the theme's label at LABEL_TOP and the menu at 200)
SPLASH_SIZE = (640, 480)
SPLASH_VARIANT, SPLASH_SCALE, SPLASH_TOP = "80", 2, 4
LABEL_TOP, LABEL_LEFT = 165, "18%"


class PatchError(Exception):
    """The netinst is not the one the patch was written for."""


def _once(text: str, old: str, new: str, where: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{where}: expected {old!r} once, found it {count} times")
    return text.replace(old, new)


def add_preseed(line: str, where: str) -> str:
    """A kernel command line with the preseed file before its `---`: the installer reads it, the
    installed system does not get it."""
    if PRESEED in line:
        raise PatchError(f"{where}: already has {PRESEED}")
    found = re.findall(r"(?<=\s)---(?=\s|$)", line)
    if len(found) != 1:
        raise PatchError(f"{where}: expected one ' --- ' in {line.strip()!r}")
    return re.sub(r"(?<=\s)---(?=\s|$)", PRESEED + " ---", line)


# --- GRUB (UEFI, amd64 and arm64)

_ENTRY = re.compile(r"^menuentry (?:--hotkey=\w )?'([^']*)' \{$")


def _top_entries(lines: Sequence[str]) -> Dict[str, Tuple[int, int]]:
    """The top-level menuentry blocks of grub.cfg: title -> (first line, line of its `}`)."""
    found: Dict[str, Tuple[int, int]] = {}
    index = 0
    while index < len(lines):
        match = _ENTRY.match(lines[index])
        if match:
            end = index + 1
            while end < len(lines) and lines[end] != "}":
                end += 1
            if end == len(lines):
                raise PatchError(f"grub.cfg: menuentry {match.group(1)!r} has no end")
            if match.group(1) in found:
                raise PatchError(f"grub.cfg: menuentry {match.group(1)!r} twice")
            found[match.group(1)] = (index, end)
            index = end
        index += 1
    return found


def _clone_entry(lines: Sequence[str], header: str) -> List[str]:
    body = list(lines[1:])
    kernels = [i for i, line in enumerate(body) if re.match(r"\s+linux\s", line)]
    if len(kernels) != 1:
        raise PatchError(f"grub.cfg: {header}: expected one linux line")
    body[kernels[0]] = add_preseed(body[kernels[0]], f"grub.cfg {header}")
    return [header] + body


def patch_grub(text: str) -> str:
    """grub.cfg with the T-Pot entries first: the first entry is the default, there is no timeout."""
    if "T-Pot" in text:
        raise PatchError("grub.cfg: already has T-Pot")
    if re.search(r"^\s*set\s+(default|timeout)=", text, re.M):
        raise PatchError("grub.cfg: sets a default or a timeout")
    lines = text.split("\n")
    entries = _top_entries(lines)
    for title in ("Install", "Graphical install"):
        if title not in entries:
            raise PatchError(f"grub.cfg: no top-level menuentry {title!r}")
    first = min(start for start, _end in entries.values())
    submenus = [i for i, line in enumerate(lines) if line.startswith("submenu ")]
    if submenus and submenus[0] < first:
        raise PatchError("grub.cfg: a submenu before the first menuentry")
    plain, gui = entries["Install"], entries["Graphical install"]
    added = (_clone_entry(lines[plain[0]:plain[1] + 1], f"menuentry --hotkey=t '{TITLE}' {{")
             + _clone_entry(lines[gui[0]:gui[1] + 1], f"menuentry '{TITLE_GUI}' {{"))
    return "\n".join(lines[:first] + added + lines[first:])


# --- ISOLINUX (BIOS, amd64)

def _label(text: str, name: str, where: str) -> Tuple[str, str]:
    """The kernel and append lines of `label NAME` in an ISOLINUX file."""
    match = re.search(rf"^label {re.escape(name)}\n((?:[ \t]+.*\n?)+)", text, re.M)
    if not match:
        raise PatchError(f"{where}: no label {name}")
    kernel = re.findall(r"^\s+kernel\s.*$", match.group(1), re.M)
    append = re.findall(r"^\s+append\s.*$", match.group(1), re.M)
    if len(kernel) != 1 or len(append) != 1:
        raise PatchError(f"{where}: label {name} needs one kernel and one append line")
    return kernel[0].strip(), append[0].strip()


def _tpot_entry(txt_cfg: str) -> Tuple[str, str]:
    """The kernel and append lines of Install T-Pot: Debian's Install (txt.cfg) with the preseed."""
    kernel, append = _label(txt_cfg, "install", "txt.cfg")
    return kernel, add_preseed(append, "txt.cfg")


def tpot_cfg(txt_cfg: str, gtk_cfg: str) -> str:
    """isolinux/tpot.cfg: the T-Pot entries, made from Debian's Install (txt.cfg) and Graphical
    install (gtk.cfg)."""
    kernel, append = _tpot_entry(txt_cfg)
    kernel_gui, append_gui = _label(gtk_cfg, "installgui", "gtk.cfg")
    return (f"default tpot\nlabel tpot\n\tmenu label Install ^T-Pot\n\tmenu default\n\t{kernel}\n"
            f"\t{append}\n"
            f"label tpotgui\n\tmenu label Install T-Pot (graphical installer)\n\t{kernel_gui}\n"
            f"\t{add_preseed(append_gui, 'gtk.cfg')}\n")


def patch_timeout(spkgtk_cfg: str, txt_cfg: str) -> str:
    """spkgtk.cfg: the timeout of the menu starts Install T-Pot instead of Debian's install with
    speech synthesis, whose entry stays in the menu. Nothing is lost by it: the installer asks for
    the language first and before it changes a disk."""
    if not re.search(r"^timeout [1-9][0-9]*$", spkgtk_cfg, re.M):
        raise PatchError("spkgtk.cfg: no timeout")
    timeouts = re.findall(r"^ontimeout .*$", spkgtk_cfg, re.M)
    if len(timeouts) != 1 or "speakup.synth=soft" not in timeouts[0]:
        raise PatchError("spkgtk.cfg: expected one ontimeout to the speech synthesis")
    kernel, append = _tpot_entry(txt_cfg)
    # the same form as Debian's: the kernel and its command line, without the words kernel and append
    text = _once(spkgtk_cfg, f"{timeouts[0]}\n",
                 f"ontimeout {kernel.split(None, 1)[1]} {append.split(None, 1)[1]}\n", "spkgtk.cfg")
    text = _once(text, "# timeout to speech-enabled install\n", "# timeout to Install T-Pot\n", "spkgtk.cfg")
    return _once(text, "menu autoboot Press a key, otherwise speech synthesis will be started in # second{,s}...\n",
                 f"menu autoboot {BIOS_AUTOBOOT}\n", "spkgtk.cfg")


def patch_isolinux(files: Dict[str, str]) -> Dict[str, str]:
    """menu.cfg, gtk.cfg, stdmenu.cfg and spkgtk.cfg (by name) changed, tpot.cfg new: T-Pot first,
    the default and what the timeout starts, Debian's Graphical install no longer the default, the
    colours of T-Pot."""
    for name in ISOLINUX_FILES:
        if name not in files:
            raise PatchError(f"isolinux/{name} is missing")
        if "T-Pot" in files[name]:
            raise PatchError(f"isolinux/{name}: already has T-Pot")
    out = {"tpot.cfg": tpot_cfg(files["txt.cfg"], files["gtk.cfg"]),
           "spkgtk.cfg": patch_timeout(files["spkgtk.cfg"], files["txt.cfg"])}
    menu = _once(files["menu.cfg"], "\ninclude gtk.cfg\n", "\ninclude tpot.cfg\ninclude gtk.cfg\n", "menu.cfg")
    # the title starts with a BEL (^G) for the speech synthesis, it stays; no longer than Debian's
    # (the width of the menu)
    out["menu.cfg"] = _once(menu, "Debian GNU/Linux installer menu (BIOS mode)",
                            BIOS_TITLE, "menu.cfg")
    gtk = _once(files["gtk.cfg"], "default installgui\n", "", "gtk.cfg")
    out["gtk.cfg"] = _once(gtk, "\tmenu default\n", "", "gtk.cfg")
    std = files["stdmenu.cfg"]
    if "#76a1d0ff" not in std:
        raise PatchError("stdmenu.cfg: no highlight colour #76a1d0ff")
    # #AARRGGBB: the magenta of T-Pot, opaque
    out["stdmenu.cfg"] = std.replace("#76a1d0ff", "#ff%02x%02x%02x" % MAGENTA)
    return out


# --- the GRUB themes

def patch_arm64_graphics(text: str) -> str:
    """Debian arm64 ships the font and graphics modules, but starts a text menu."""
    return _once(text, "set menu_color_normal=cyan/blue\nset menu_color_highlight=white/blue\n",
                 """set menu_color_normal=white/black
set menu_color_highlight=white/magenta

set tpot_font=/boot/grub/font.pf2
# Secure Boot permits the font embedded in Debian's signed GRUB, not an external font.
if [ -f (memdisk)/fonts/unicode.pf2 ]; then
  set tpot_font=(memdisk)/fonts/unicode.pf2
fi
if loadfont $tpot_font; then
  insmod all_video
  insmod gfxterm
  insmod gfxmenu
  insmod png
  set gfxmode=800x600,auto
  if terminal_output gfxterm; then
    set theme=/boot/grub/tpot/theme.txt
  fi
fi
""", "grub.cfg (arm64)")


ARM64_THEME = """title-text: ""
desktop-color: "black"
desktop-image: "/boot/grub/tpot/splash.png"

+ label {
  top = 165
  left = 18%
  text = "T-Pot installer on Debian (UEFI)"
  font = "GNU Unifont Regular 16"
  color = "#c0c0c0"
}

+ boot_menu {
  top = 200
  left = 10%
  width = 80%
  height = 55%
  item_font = "GNU Unifont Regular 16"
  item_color = "#c0c0c0"
  selected_item_color = "white"
  selected_item_pixmap_style = "hl_*.png"
  item_height = 24
  item_spacing = 2
}

+ label {
  top = 94%
  left = 10%
  text = "Up/Down: select   Enter: boot   E: edit   C: command line"
  font = "GNU Unifont Regular 16"
  color = "#c0c0c0"
}
"""


def patch_theme(text: str, name: str) -> Optional[str]:
    """A theme of /boot/grub/theme with the background (the light ones): the title off the logo, the
    label below it, T-Pot's highlight. None for the others (the dark contrast ones)."""
    if 'desktop-image: "/isolinux/splash.png"' not in text:
        return None
    where = f"theme/{name}"
    match = re.search(r'^title-text: "Debian GNU/Linux ([^"]*)"$', text, re.M)
    if not match:
        raise PatchError(f"{where}: no title-text of Debian")
    text = text.replace(match.group(0), 'title-text: ""')
    text = _once(text, "+ vbox {\n  top = 80\n  left = 45%\n",
                 f"+ vbox {{\n  top = {LABEL_TOP}\n  left = {LABEL_LEFT}\n", where)
    text = _once(text, '"Debian GNU/Linux UEFI Installer menu"',
                 f'"T-Pot installer on Debian GNU/Linux {match.group(1)} (UEFI)"', where)
    return _once(text, 'selected_item_color = "black"', 'selected_item_color = "white"', where)


# --- PNG

def png_rgb(width: int, height: int, pixels: bytes) -> bytes:
    """An 8-bit RGB PNG of width x height pixels (3 bytes each, row by row)."""
    if len(pixels) != width * height * 3:
        raise ValueError("pixels do not match the size")
    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def png_size(data: bytes) -> Tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise PatchError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def splash_png() -> bytes:
    """The background: the T-Pot logo of the Manager (splash_art), top centre, on black."""
    width, height = SPLASH_SIZE
    logo_w, logo_h, logo = splash_art.grid(SPLASH_VARIANT)
    left = (width - logo_w * SPLASH_SCALE) // 2
    canvas = bytearray(width * height * 3)
    for y in range(logo_h):
        for x in range(logo_w):
            colour = bytes(splash_art.PALETTE[logo[y * logo_w + x]])
            for dy in range(SPLASH_SCALE):
                row = SPLASH_TOP + y * SPLASH_SCALE + dy
                start = (row * width + left + x * SPLASH_SCALE) * 3
                canvas[start:start + 3 * SPLASH_SCALE] = colour * SPLASH_SCALE
    return png_rgb(width, height, bytes(canvas))


def solid_png(width: int, height: int, colour: Tuple[int, int, int] = MAGENTA) -> bytes:
    return png_rgb(width, height, bytes(colour) * (width * height))


# --- the tree

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def patch_tree(arch: str, orig: str, out: str) -> List[str]:
    """Writes the changed and new boot files of an ISO (extracted to orig) to out: their paths."""
    written: Dict[str, bytes] = {}
    grub = os.path.join(orig, "boot", "grub", "grub.cfg")
    if not os.path.isfile(grub):
        raise PatchError("boot/grub/grub.cfg is missing")
    grub_text = patch_grub(_read(grub))
    if arch == "arm64":
        if not os.path.isfile(os.path.join(orig, "boot/grub/font.pf2")):
            raise PatchError("boot/grub/font.pf2 is missing")
        grub_text = patch_arm64_graphics(grub_text)
        written["boot/grub/tpot/theme.txt"] = ARM64_THEME.encode()
        written["boot/grub/tpot/splash.png"] = splash_png()
        written["boot/grub/tpot/hl_c.png"] = solid_png(20, 20)
    written["boot/grub/grub.cfg"] = grub_text.encode()
    if arch == "amd64":
        isolinux = os.path.join(orig, "isolinux")
        files = {name: _read(os.path.join(isolinux, name))
                 for name in ISOLINUX_FILES
                 if os.path.isfile(os.path.join(isolinux, name))}
        for name, text in patch_isolinux(files).items():
            written[f"isolinux/{name}"] = text.encode()
        written["isolinux/splash.png"] = splash_png()
        themes = os.path.join(orig, "boot", "grub", "theme")
        patched = 0
        for name in sorted(os.listdir(themes)) if os.path.isdir(themes) else []:
            path = os.path.join(themes, name)
            if name.endswith(".png") or not os.path.isfile(path):
                continue
            text = patch_theme(_read(path), name)
            if text is not None:
                written[f"boot/grub/theme/{name}"] = text.encode()
                patched += 1
        if not patched:
            raise PatchError("boot/grub/theme: no theme with the background")
        highlight = os.path.join(themes, "hl_c.png")
        if not os.path.isfile(highlight):
            raise PatchError("boot/grub/theme/hl_c.png is missing")
        with open(highlight, "rb") as handle:
            written["boot/grub/theme/hl_c.png"] = solid_png(*png_size(handle.read()))
    elif arch != "arm64":
        raise PatchError(f"unknown architecture {arch}")
    for rel, data in written.items():
        path = os.path.join(out, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    return sorted(written)


def update_md5sum(text: str, files: Dict[str, bytes]) -> str:
    """md5sum.txt (`<md5>  ./<path>` lines) with the md5 of files (path in the ISO -> content): a
    line of a path updated, a new path added at the end."""
    lines = [line for line in text.split("\n") if line]
    index = {}
    for number, line in enumerate(lines):
        match = re.match(r"^[0-9a-f]{32}  \./(.+)$", line)
        if not match:
            raise PatchError(f"md5sum.txt: line {number + 1} is not '<md5>  ./<path>'")
        index[match.group(1)] = number
    for rel in sorted(files):
        line = f"{hashlib.md5(files[rel]).hexdigest()}  ./{rel}"
        if rel in index:
            lines[index[rel]] = line
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def tree_files(folder: str) -> Dict[str, bytes]:
    """The files under folder by their path in the ISO, md5sum.txt left out."""
    found = {}
    for root, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            if rel != "md5sum.txt":
                with open(path, "rb") as handle:
                    found[rel] = handle.read()
    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="isotree.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    patch = sub.add_parser("patch")
    patch.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    patch.add_argument("--orig", required=True)
    patch.add_argument("--out", required=True)
    md5 = sub.add_parser("md5sum")
    md5.add_argument("--orig", required=True)
    md5.add_argument("--out", required=True)
    splash = sub.add_parser("splash")
    splash.add_argument("file")
    args = parser.parse_args(argv)
    try:
        if args.command == "patch":
            for rel in patch_tree(args.arch, args.orig, args.out):
                print(rel)
        elif args.command == "md5sum":
            text = update_md5sum(_read(args.orig), tree_files(args.out))
            with open(os.path.join(args.out, "md5sum.txt"), "w", encoding="utf-8") as handle:
                handle.write(text)
        else:
            with open(args.file, "wb") as handle:
                handle.write(splash_png())
    except (PatchError, OSError) as error:
        print(f"isotree.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
