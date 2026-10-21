#!/usr/bin/env python3
"""Build the texts of the persona's file commands (docker/cowrie/dist/tpot_personas/fs_texts.json).

rm, cp, mv, touch, rmdir and mkdir of the patched Cowrie (MKDIR_COMMAND of the patch script) answer with the
texts of the real tool of the persona: GNU coreutils of a release or the BusyBox of a firmware. Their texts
come from recordings of the real programs (docker/cowrie/tests/fixtures/fs_tools.json, the full --help in
fs_help.json); this script turns them into one object per variant (the key of the recording):

  style      gnu or busybox
  version    the coreutils or BusyBox version
  libc       the C library (the texts of option errors come from the persona's [shell] text_libc)
  help       {tool: the whole --help} (GNU)
  usage      {tool: the text after "Usage: TOOL "} (BusyBox; the banner line comes from [shell] text_banner)
  help_status  the exit status of --help (BusyBox; 1 up to 1.19.4)
  verbose    {tool: whether -v prints} (BusyBox without FEATURE_VERBOSE prints nothing)
  options    {tool: {"short": getopt string, "long": {name: [key, 0 none / 1 required / 2 optional]}}}
  templates  {condition: text}, made by putting placeholders for the arguments of a recorded case into its
             output: {N} the value as it is, {N:q} the value as GNU's quoteaf quotes it (quotes included);
             a reason (strerror) of a case becomes a placeholder too, so "cp.create" serves every errno; a
             variant that prints another text for one errno (BusyBox's "Path does not exist") gets
             "cp.create.ENOENT" for it
  rm_kinds   {kind: the word of rm's prompt} (GNU)

Templates the recordings do not cover (rm -v of a folder, cp of a file onto itself, ...) come from SOURCE
below, each with its source; they are marked unverified there. One variant is derived, not recorded:
src_busybox_1.14.1 (the D-Link firmware's BusyBox, no image of it could be run) is fw_tplink_c7v5 with the
usage texts of 1.14.1 and its "cannot" (busybox_sources.md of the recording notes).

Run it with `python3 -I docker/cowrie/tools/build_fs_texts.py [--check]`; --check compares the written file
with a fresh build (the tests run it).
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "tests" / "fixtures"
OUT = HERE.parent / "dist" / "tpot_personas" / "fs_texts.json"
TOOLS = ("rm", "cp", "mv", "touch", "rmdir", "mkdir")
ERRNO = {"ENOENT": "No such file or directory", "ENOTDIR": "Not a directory", "EISDIR": "Is a directory",
         "EACCES": "Permission denied", "ENOTEMPTY": "Directory not empty", "EINVAL": "Invalid argument",
         "EEXIST": "File exists"}

# (case, stream, line or None for all of it, key, arguments, errno of the reason or None, style or None)
#   the arguments as the case wrote them; a template is made where the recorded text is no option error
RULES = (
    ("rm", 1, None, "rm.missing_operand", (), None, "gnu"),
    ("rm -d d", 1, None, "rm.cannot_remove", ("d",), "ENOTEMPTY", "gnu"),
    ("rm -i file </dev/null", 1, None, "rm.prompt", ("file", "regular empty file"), None, "gnu"),
    ("rm -i file </dev/null", 1, None, "rm.prompt", ("file",), None, "busybox"),
    ("rm -rf /", 1, None, "rm.preserve_root", ("/",), None, "gnu"),
    ("rm -v file", 0, None, "rm.removed", ("file",), None, None),
    ("rm /", 1, None, "rm.cannot_remove", ("/",), "EISDIR", "gnu"),
    ("rm /", 1, None, "rm.is_dir", ("/",), None, "busybox"),
    ("rm d", 1, None, "rm.cannot_remove", ("d",), "EISDIR", "gnu"),
    ("rm d", 1, None, "rm.is_dir", ("d",), None, "busybox"),
    ("rm x", 1, None, "rm.cannot_remove", ("x",), "ENOENT", None),
    ("rm -f /r/x", 1, None, "rm.cannot_remove", ("/r/x",), "EACCES", None),
    ("rm /r/x", 1, None, "rm.cannot_remove", ("/r/x",), "ENOENT", None),
    ("rm /r/y", 1, None, "rm.cannot_remove", ("/r/y",), "EACCES", None),
    ("cp", 1, None, "cp.missing_operand", (), None, "gnu"),
    ("cp a b file", 1, None, "cp.target", ("file",), "ENOTDIR", "gnu"),
    ("cp a b file", 1, 0, "cp.stat", ("file/a",), "ENOTDIR", "busybox"),
    ("cp a b t2/", 1, None, "cp.target", ("t2/",), "ENOENT", "gnu"),
    ("cp a b t2/", 1, 0, "cp.create", ("t2/a",), "ENOENT", "busybox"),
    ("cp a nosuch/", 1, None, "cp.create", ("nosuch/",), "ENOTDIR", "gnu"),
    ("cp a nosuch/", 1, None, "cp.create", ("nosuch/",), "EISDIR", "busybox"),
    ("cp a nosuch/x", 1, None, "cp.create", ("nosuch/x",), "ENOENT", None),
    ("cp a /r/x", 1, None, "cp.create", ("/r/x",), "EACCES", None),
    ("cp d y", 1, None, "cp.omit_dir", ("d",), None, None),
    ("cp file", 1, None, "cp.missing_dest", ("file",), None, "gnu"),
    ("cp x y", 1, None, "cp.stat", ("x",), "ENOENT", None),
    ("cp -v a a2", 0, None, "cp.verbose", ("a", "a2"), None, None),
    ("mv", 1, None, "mv.missing_operand", (), None, "gnu"),
    ("mv -v a a2", 0, None, "mv.verbose", ("a", "a2"), None, None),
    ("mv a b file", 1, None, "mv.target", ("file",), "ENOTDIR", "gnu"),
    ("mv a b file", 1, 0, "mv.stat", ("file/a",), "ENOTDIR", "busybox"),
    ("mv a nosuch/x", 1, None, "mv.cannot_move", ("a", "nosuch/x"), "ENOENT", "gnu"),
    ("mv a nosuch/x", 1, None, "mv.rename", ("a",), "ENOENT", "busybox"),
    ("mv a /r/x", 1, None, "mv.cannot_move", ("a", "/r/x"), "EACCES", "gnu"),
    ("mv a /r/x", 1, None, "mv.rename", ("a",), "EACCES", "busybox"),
    ("mv d d/sub", 1, None, "mv.subdir", ("d", "d/sub"), None, "gnu"),
    ("mv d d/sub", 1, None, "mv.rename", ("d",), "EINVAL", "busybox"),
    ("mv file", 1, None, "mv.missing_dest", ("file",), None, "gnu"),
    ("mv x y", 1, None, "mv.stat", ("x",), "ENOENT", "gnu"),
    ("mv x y", 1, None, "mv.rename", ("x",), "ENOENT", "busybox"),
    ("rmdir", 1, None, "rmdir.missing_operand", (), None, "gnu"),
    ("rmdir d", 1, None, "rmdir.failed", ("d",), "ENOTEMPTY", None),
    ("rmdir file", 1, None, "rmdir.failed", ("file",), "ENOTDIR", None),
    ("rmdir x", 1, None, "rmdir.failed", ("x",), "ENOENT", None),
    ("rmdir /r", 1, None, "rmdir.failed", ("/r",), "EACCES", None),
    ("mkdir -p z/y; touch z/k; rmdir -p z/y", 1, None, "rmdir.failed_parent", ("z",), "ENOTEMPTY", "gnu"),
    ("mkdir -p z/y; touch z/k; rmdir -p z/y", 1, None, "rmdir.failed", ("z",), "ENOTEMPTY", "busybox"),
    ("mkdir -p v/w; rmdir -pv v/w", 0, 0, "rmdir.verbose", ("v/w",), None, None),
    ("touch", 1, None, "touch.missing_operand", (), None, "gnu"),
    ("touch -d 'not a date' f6", 1, None, "touch.invalid_date", ("not a date",), None, None),
    ("touch -t 99999 f5", 1, None, "touch.invalid_date", ("99999",), None, None),
    ("touch -r nosuch f7", 1, None, "touch.reference", ("nosuch",), "ENOENT", None),
    ("touch nosuch/x", 1, None, "touch.cannot_touch", ("nosuch/x",), "ENOENT", None),
    ("touch /r/x", 1, None, "touch.cannot_touch", ("/r/x",), "EACCES", None),
    ("mkdir /tmp", 1, None, "mkdir.cannot_create", ("/tmp",), "EEXIST", None),
    ("mkdir -pv d1/d2", 0, 0, "mkdir.verbose", ("d1",), None, "gnu"),
    ("mkdir -pv d1/d2", 0, 0, "mkdir.verbose", ("d1/",), None, "busybox"),
)

# Templates of conditions the recordings do not hold, from the sources (unverified against a running tool):
#   GNU coreutils: remove.c (removed directory, descend into, refusing . and ..), copy.c (same file, into
#   itself, overwrite, the overwrite prompt), mv.c, rm.c (the prompt of -I), touch.c (setting times), mkdir.c
#   (invalid mode, quote()), the missing and extra operand texts of its siblings;
#   "curly" is the form of 8.22 / 8.23 (quote() of a UTF-8 locale for every name, the colon of 8.22);
#   BusyBox: libbb/remove_file.c, libbb/copy_file.c, coreutils/rm.c and mkdir.c of 1.19.4 to 1.36.1.
# Conditions the recordings do not hold that take a recorded template with another errno, from the same
#   sources (unverified as the templates above): rm of a link to a folder named with a slash at its end
#   ("Is a directory" without -r; with -r the folder is emptied, then "Not a directory": remove.c,
#   remove_file.c), rm -i of a folder that still holds a declined node ("Directory not empty"), cp of a name
#   longer than PATH_MAX ("File name too long"); cp -r, -a, -P and -d copy a link as a link (cp.c of
#   coreutils, DEREF_NEVER with -R; coreutils/cp.c of BusyBox, "Preserve symlinks (default if -R)").
SOURCE = {
    "gnu": {
        "rm.removed_dir": ("removed directory {0:q}\n", "removed directory: \u2018{0}\u2019\n"),
        "rm.descend": ("rm: descend into directory {0:q}? ", None),
        "rm.dot": ("rm: refusing to remove '.' or '..' directory: skipping {0:q}\n",
                   "rm: cannot remove directory: \u2018{0}\u2019\n"),
        "cp.same": ("cp: {0:q} and {1:q} are the same file\n", None),
        "cp.into_itself": ("cp: cannot copy a directory, {0:q}, into itself, {1:q}\n", None),
        "cp.nondir": ("cp: cannot overwrite non-directory {0:q} with directory {1:q}\n", None),
        "mv.same": ("mv: {0:q} and {1:q} are the same file\n", None),
        "mv.nondir": ("mv: cannot overwrite non-directory {0:q} with directory {1:q}\n", None),
        "mv.dir": ("mv: cannot overwrite directory {0:q} with non-directory\n", None),
        "rm.prompt_some": ("rm: remove {0} arguments? ", None),
        "rm.prompt_some_one": ("rm: remove {0} argument? ", None),
        "rm.prompt_some_r": ("rm: remove {0} arguments recursively? ", None),
        "rm.prompt_some_r_one": ("rm: remove {0} argument recursively? ", None),
        "cp.overwrite": ("cp: overwrite {0:q}? ", None),
        "cp.mkdir": ("cp: cannot create directory {0:q}: {1}\n", None),
        "cp.dir": ("cp: cannot overwrite directory {0:q} with non-directory\n", None),
        "cp.extra": ("cp: extra operand {0:q}\nTry 'cp --help' for more information.\n", None),
        "mv.overwrite": ("mv: overwrite {0:q}? ", None),
        "mv.extra": ("mv: extra operand {0:q}\nTry 'mv --help' for more information.\n", None),
        "touch.setting_times": ("touch: setting times of {0:q}: {1}\n", None),
        "mkdir.missing_operand": ("mkdir: missing operand\nTry 'mkdir --help' for more information.\n", None),
        # quote() of mkdir.c, as "cannot create directory" of the same variant
        "mkdir.invalid_mode": ("mkdir: invalid mode \u2018{0}\u2019\n", None),
    },
    "busybox": {
        "rm.removed_dir": ("removed directory: '{0}'\n", None),
        "rm.descend": ("rm: descend into directory '{0}'? ", None),
        "rm.prompt_dir": ("rm: remove directory '{0}'? ", None),
        "rm.dot": ("rm: can't remove '.' or '..'\n", None),
        "rm.stat": ("rm: can't stat '{0}': {1}\n", None),
        "cp.overwrite": ("cp: overwrite '{0}'? ", None),
        "cp.mkdir": ("cp: can't create directory '{0}': {1}\n", None),
        "mv.overwrite": ("mv: overwrite '{0}'? ", None),
        "cp.same": ("cp: '{0}' and '{1}' are the same file\n", None),
        "cp.recursion": ("cp: recursion detected, omitting directory '{0}'\n", None),
        "cp.target_not_dir": ("cp: target '{0}' is not a directory\n", None),
        "mkdir.invalid_mode": ("mkdir: invalid mode '{0}'\n", None),
    },
}
# the words of GNU rm's prompt by file type (gnulib file_type(); the recordings hold "regular empty file")
RM_KINDS = {"empty": "regular empty file", "file": "regular file", "dir": "directory", "link": "symbolic link"}
# "Written by" of --version (the AUTHORS of each program in the coreutils sources)
AUTHORS = {"rm": "Paul Rubin, David MacKenzie, Richard M. Stallman,\nand Jim Meyering.",
           "cp": "Torbjorn Granlund, David MacKenzie, and Jim Meyering.",
           "mv": "Mike Parker, David MacKenzie, and Jim Meyering.",
           "touch": "Paul Rubin, Arnold Robbins, Jim Kingdon,\nDavid MacKenzie, and Randy Smith.",
           "rmdir": "David MacKenzie.", "mkdir": "David MacKenzie."}

# BusyBox 1.14.1 (the D-Link persona): the usage texts of include/usage.h of busybox-1.14.1.tar.bz2 (sha256
#   5165792f..., the recording notes busybox_sources.md), without SELinux and the long options, as the
#   minimal build of the Archer C7 v5 has them; bb_show_usage of 1.14 to 1.20 ends with a blank line
USAGE_1_14_1 = {
    "rm": "[OPTION]... FILE...\n\nRemove (unlink) the FILE(s). Use '--' to\nindicate that all following arguments "
          "are non-options.\n\nOptions:\n\t-i\tAlways prompt before removing\n\t-f\tNever prompt\n"
          "\t-r,-R\tRemove directories recursively\n\n",
    "cp": "[OPTION]... SOURCE DEST\n\nCopy SOURCE to DEST, or multiple SOURCE(s) to DIRECTORY\n\nOptions:\n"
          "\t-a\tSame as -dpR\n\t-d,-P\tPreserve links\n\t-H,-L\tDereference all symlinks (default)\n"
          "\t-p\tPreserve file attributes if possible\n\t-f\tForce overwrite\n\t-i\tPrompt before overwrite\n"
          "\t-R,-r\tRecurse directories\n\t-l,-s\tCreate (sym)links\n\n",
    "mv": "[OPTION]... SOURCE DEST\nor: mv [OPTION]... SOURCE... DIRECTORY\n\nRename SOURCE to DEST, or move "
          "SOURCE(s) to DIRECTORY\n\nOptions:\n\t-f\tDon't prompt before overwriting\n"
          "\t-i\tInteractive, prompt before overwrite\n\n",
    "touch": "[-c] FILE [FILE...]\n\nUpdate the last-modified date on the given FILE[s]\n\nOptions:\n"
             "\t-c\tDo not create any files\n\n",
    "rmdir": "[OPTION]... DIRECTORY...\n\nRemove the DIRECTORY, if it is empty.\n\nOptions:\n\t-p\tInclude parents\n\n",
    "mkdir": "[OPTION] DIRECTORY...\n\nCreate DIRECTORY\n\nOptions:\n"
             "\t-m\tSet permission mode (as in chmod), not rwxrwxrwx - umask\n"
             "\t-p\tNo error if existing, make parent directories as needed\n\n",
}
# the options of 1.14.1 (getopt32 strings of coreutils/*.c and FILEUTILS_CP_OPTSTR of include/libbb.h); touch
#   without CONFIG_DESKTOP (as the minimal build): -c and the ignored -f -m -a
OPTIONS_1_14_1 = {"rm": "fiRrv", "cp": "pdRfilsLarPHv", "mv": "fi", "touch": "cfma", "rmdir": "p", "mkdir": "m:p"}


def busybox_options(version: tuple, long_options: bool, verbose: bool, usage: dict) -> dict:
    """The getopt32 strings and long options of a BusyBox, from its sources (coreutils/*.c, FILEUTILS_CP_OPTSTR
    of include/libbb.h of 1.19.4, 1.24.1, 1.26.2, 1.27.2, 1.33.2, 1.35.0 and 1.36.1; 1.25 and 1.28 to 1.32 as
    the next older source whose usage text they print) and the build options their usage shows (touch -d / -h)."""
    if version >= (1, 34):
        cp = "pdRfinlsLHarPvuTt:"
    elif version >= (1, 28):
        cp = "pdRfilsLHarPvuT"
    elif version >= (1, 25):
        cp = "pdRfilsLHarPvu"
    else:
        cp = "pdRfilsLHarPv"
    mv = "finTt:v" if version >= (1, 34) else "finv" if version >= (1, 24) else "fin"
    susv3 = "-d DT" in usage.get("touch", "")
    noderef = "\t-h\t" in usage.get("touch", "")
    if version >= (1, 34):
        touch = "ch" + ("r:d:t:am" if susv3 else "") + "f" + ("" if susv3 else "am")
    else:
        touch = "c" + ("r:d:t:" if susv3 else "") + ("h" if noderef else "") + "fma"
    small = version >= (1, 24)
    shorts = {"rm": "fiRrv", "cp": cp, "mv": mv, "touch": touch, "rmdir": "pv" if small else "p",
              "mkdir": "m:pv" if small else "m:p"}
    longs = {tool: {} for tool in TOOLS}
    if long_options:
        cp_long = {"archive": ["a", 0], "force": ["f", 0], "interactive": ["i", 0], "link": ["l", 0],
                   "dereference": ["L", 0], "no-dereference": ["P", 0], "recursive": ["R", 0],
                   "symbolic-link": ["s", 0], "verbose": ["v", 0], "update": ["u", 0],
                   "remove-destination": ["remove-destination", 0], "parents": ["parents", 0]}
        if version >= (1, 28):
            cp_long["no-target-directory"] = ["T", 0]
        if version >= (1, 34):
            cp_long.update({"no-clobber": ["n", 0], "target-directory": ["t", 1]})
        mv_long = {"interactive": ["i", 0], "force": ["f", 0], "no-clobber": ["n", 0]}
        if verbose:
            mv_long["verbose"] = ["v", 0]
        if version >= (1, 34):
            mv_long.update({"no-target-directory": ["T", 0], "target-directory": ["t", 1]})
        touch_long = {"no-create": ["c", 0]}
        if susv3:
            touch_long.update({"reference": ["r", 1], "date": ["d", 1]})
        if noderef or version >= (1, 34):
            touch_long["no-dereference"] = ["h", 0]
        rmdir_long = {"parents": ["p", 0], "ignore-fail-on-non-empty": ["ignore-fail-on-non-empty", 0]}
        mkdir_long = {"mode": ["m", 1], "parents": ["p", 0]}
        if verbose and small:
            rmdir_long["verbose"] = ["v", 0]
            mkdir_long["verbose"] = ["v", 0]
        longs.update(cp=cp_long, mv=mv_long, touch=touch_long, rmdir=rmdir_long, mkdir=mkdir_long)
    return {tool: {"short": shorts[tool], "long": longs[tool]} for tool in TOOLS}


HELP_LINE = re.compile(r"^ {2,6}(-[^ ].*?)(?: {2,}|$)")


def gnu_options(help_text: str) -> dict:
    """The options of a GNU tool from its --help: the getopt string of its short options (a short that shares a
    line with a long one of "=ARG", or is written "-t STAMP" / "-t [[CC]YY]...", takes an argument) and its long options (key: the
    short letter, else the name; 0 no argument, 1 required "=ARG", 2 optional "[=ARG]")."""
    shorts, longs = "", {}
    for line in help_text.splitlines():
        found = HELP_LINE.match(line)
        if not found or found.group(1).startswith("- "):
            continue
        names = [part.strip() for part in found.group(1).split(", ")]
        letters, takes = [], 0
        for name in names:
            word = re.fullmatch(r"--([\w-]+)(\[=[^\]]+\]|=\S+)?", name)
            if word:
                takes = 0 if not word.group(2) else 2 if word.group(2).startswith("[") else 1
                continue
            letter = re.fullmatch(r"-(\w)( [A-Z\[][^ ]*)?", name)
            if letter:
                letters.append(letter.group(1))
                if letter.group(2):
                    takes = 1
        for name in names:
            word = re.fullmatch(r"--([\w-]+)(\[=[^\]]+\]|=\S+)?", name)
            if word:
                wanted = 0 if not word.group(2) else 2 if word.group(2).startswith("[") else 1
                longs[word.group(1)] = [letters[0] if letters else word.group(1), wanted]
        for letter in letters:
            if letter not in shorts:
                shorts += letter + (":" if takes == 1 else "")
    longs.setdefault("help", ["help", 0])
    longs.setdefault("version", ["version", 0])
    return {"short": shorts, "long": longs}


def quoted(text: str, value: str, start: int):
    """The first place of value at or after start: (begin, end, kind) with kind "'" (in ASCII quotes), "\u2018"
    (in the quotes of a UTF-8 locale) or "" (bare, not inside a longer name)."""
    found = []
    for left, right, kind in (("'", "'", "'"), ("\u2018", "\u2019", "\u2018")):
        at = text.find(left + value + right, start)
        if at >= 0:
            found.append((at, at + len(value) + 2, kind))
    if found:
        return min(found)
    bare = re.compile(r"(?<![\w/.'\u2018-])" + re.escape(value) + r"(?![\w/.'\u2019])").search(text, start)
    return (bare.start(), bare.end(), "") if bare else None


def template_of(text: str, args, errno, gnu_quoteaf: bool):
    """The template of a recorded text: each argument a placeholder (its number in args; the first place of
    each, the arguments do not overlap), the reason of errno one too. Returns (template, has the reason)."""
    spots, taken = [], []
    for index, value in enumerate(args):
        at = 0
        while True:
            spot = quoted(text, value, at)
            if spot is None:
                raise ValueError(f"{value!r} not in {text!r}")
            if not any(spot[0] < end and begin < spot[1] for begin, end in taken):
                break
            at = spot[0] + 1
        taken.append(spot[:2])
        spots.append((spot, index))
    out, at = "", 0
    for (begin, end, kind), index in sorted(spots):
        out += text[at:begin]
        if kind == "'" and gnu_quoteaf:
            out += f"{{{index}:q}}"
        elif kind == "'":
            out += f"'{{{index}}}'"
        elif kind == "\u2018":
            out += f"\u2018{{{index}}}\u2019"
        else:
            out += f"{{{index}}}"
        at = end
    rest = text[at:]
    reason = ERRNO.get(errno) if errno else None
    if reason and reason in rest:
        cut = rest.rfind(reason)
        return out + rest[:cut] + f"{{{len(args)}}}" + rest[cut + len(reason):], True
    return out + rest, False


def stream_text(result, stream, line):
    text = result[stream]
    if line is None:
        return text
    lines = text.splitlines(keepends=True)
    return lines[line] if line < len(lines) else ""


def version_of(kind: str, cases: dict) -> tuple:
    if kind.startswith("gnu"):
        return tuple(int(x) for x in kind.split()[1].split("."))
    banner = re.search(r"BusyBox v(\d+)\.(\d+)\.(\d+)", cases["rm"][1])
    return tuple(int(x) for x in banner.groups())


def usage_of(tool: str, text: str) -> str:
    found = re.search(r"\nUsage: " + tool + " (.*)", text, re.S)
    if not found:
        raise ValueError(f"no usage of {tool} in {text!r}")
    return found.group(1)


def build_templates(style: str, version: tuple, cases: dict, quote_keys: set) -> dict:
    """The templates of one variant from its cases (RULES), then those of SOURCE it lacks."""
    gnu = style == "gnu"
    found: dict = {}
    for case, stream, line, key, args, errno, only in RULES:
        if only not in (None, style) or case not in cases:
            continue
        text = stream_text(cases[case], stream, line)
        if not text or "multi-call binary" in text or "option" in text:
            continue
        quoteaf = gnu and version >= (8, 25) and key not in quote_keys
        found.setdefault(key, []).append((template_of(text, args, errno, quoteaf), errno))
    templates = {}
    for key, entries in found.items():
        generic = {template for (template, reason), _ in entries if reason}
        literal = {(template, errno) for (template, reason), errno in entries if not reason}
        if len(generic) > 1:
            raise ValueError(f"{key}: the cases disagree: {sorted(generic)}")
        if generic:
            templates[key] = generic.pop()
            for template, errno in sorted(literal, key=lambda x: (x[1] or "", x[0])):
                templates[f"{key}.{errno}"] = template
        elif len({template for template, _ in literal}) == 1:
            templates[key] = literal.pop()[0]
        else:
            raise ValueError(f"{key}: no common text: {sorted(literal)}")
    curly = gnu and version < (8, 25)
    for key, (text, old) in SOURCE[style].items():
        if key in templates:
            continue
        if curly:
            text = old or re.sub(r"\{(\d+):q\}", "\u2018{\\1}\u2019", text)
        elif gnu and "\u2018" in text and "{0:q}" not in text and templates.get("mkdir.cannot_create", "").find(
                "\u2018") < 0:
            # quote() in the POSIX locale: ASCII quotes, as "cannot create directory" of the same variant
            text = text.replace("\u2018", "'").replace("\u2019", "'")
        templates[key] = text
    return dict(sorted(templates.items()))


def quote_keys_of(images: dict) -> set:
    """The keys whose argument GNU quotes with quote() (the quotes of the locale), not quoteaf: those the
    recording of a UTF-8 coreutils from 8.25 on (debian_12, 9.1) writes in curly quotes."""
    cases = images["debian_12"]["cases"]
    keys = set()
    for case, stream, line, key, args, errno, only in RULES:
        if only in (None, "gnu") and case in cases and "\u2018" in stream_text(cases[case], stream, line):
            keys.add(key)
    return keys


def build() -> dict:
    tools = json.loads((FIXTURES / "fs_tools.json").read_text(encoding="utf-8"))["images"]
    helps = json.loads((FIXTURES / "fs_help.json").read_text(encoding="utf-8"))["images"]
    quote_keys = quote_keys_of(tools)
    variants = {}
    for key, image in tools.items():
        if key.endswith("_user"):
            continue
        cases = dict(image["cases"])
        cases.update(tools.get(key + "_user", {}).get("cases", {}))
        style = "gnu" if image["kind"].startswith("gnu") else "busybox"
        version = version_of(image["kind"], cases)
        variant = {"style": style, "version": ".".join(map(str, version)),
                   "libc": "glibc" if style == "gnu" else image["kind"].split()[1],
                   "recorded": image["image"]}
        if style == "gnu":
            # debian_12_en: the help of debian_12 (the same in every recorded case, report of the recording)
            source = helps.get(key) or helps.get(key.removesuffix("_en")) or {"cases": {}}
            help_texts = {tool: source["cases"][f"{tool} --help"][0] for tool in TOOLS
                          if f"{tool} --help" in source["cases"]}
            for tool in ("rm", "mkdir"):
                help_texts[tool] = cases[f"{tool} --help"][0]
            partial = []
            for tool in TOOLS:
                if tool not in help_texts:
                    # debian_8 / debian_9: the help only as far as recorded (no persona uses them)
                    head = cases.get(f"{tool} --help | head -3")
                    help_texts[tool] = head[0] if head else ""
                    partial.append(tool)
            variant["help"] = dict(sorted(help_texts.items()))
            if partial:
                variant["help_partial"] = partial
            # 8.22 / 8.23 have the help of their own; the options of a partial help come from 8.22 (centos_7)
            options = {}
            for tool in TOOLS:
                text = help_texts[tool] if tool not in partial else helps["centos_7"]["cases"][f"{tool} --help"][0]
                options[tool] = gnu_options(text)
            variant["options"] = options
            variant["rm_kinds"] = dict(RM_KINDS)
        else:
            usage = {tool: usage_of(tool, cases[tool][1]) for tool in TOOLS if tool != "mkdir"}
            usage["mkdir"] = usage_of("mkdir", cases["mkdir --help"][1])
            variant["usage"] = dict(sorted(usage.items()))
            variant["banner"] = re.match(r"(BusyBox v[^\n]*?) multi-call binary", cases["rm"][1]).group(1)
            variant["help_status"] = cases["rm --help"][2]
            long_options = "unrecognized" not in cases["rmdir --ignore-fail-on-non-empty d; echo $?"][1]
            variant["long_options"] = long_options
            verbose = bool(cases["rm -v file"][0])
            variant["verbose"] = {tool: verbose for tool in TOOLS}
            variant["options"] = busybox_options(version, long_options, verbose, usage)
        variant["templates"] = build_templates(style, version, cases, quote_keys)
        variants[key] = variant
    variants["src_busybox_1.14.1"] = derived_1_14_1(variants["fw_tplink_c7v5"])
    return {"_about": "Texts of the persona's rm, cp, mv, touch, rmdir and mkdir per variant, made by "
                      "docker/cowrie/tools/build_fs_texts.py from tests/fixtures/fs_tools.json and fs_help.json "
                      "(recordings of the real programs); do not edit.",
            "authors": AUTHORS, "variants": dict(sorted(variants.items()))}


def derived_1_14_1(c7: dict) -> dict:
    """BusyBox 1.14.1 of the D-Link firmware, derived (no image of it could be run): the Archer C7 v5 variant
    (a minimal build: no long options, no FEATURE_VERBOSE, touch without -d / -r / -t) with the usage texts
    of 1.14.1 and its "cannot" where 1.17 and later write "can't" (libbb of busybox-1.14.1)."""
    variant = json.loads(json.dumps(c7))
    variant.update(version="1.14.1", recorded="derived: fw_tplink_c7v5 with the usage and texts of the "
                                              "busybox-1.14.1 sources (not recorded)",
                   banner="BusyBox v1.14.1", usage=dict(sorted(USAGE_1_14_1.items())))
    variant["options"] = {tool: {"short": OPTIONS_1_14_1[tool], "long": {}} for tool in TOOLS}
    variant["templates"] = {key: text.replace("can't", "cannot") for key, text in variant["templates"].items()}
    return variant


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv):
    text = render(build())
    if argv[1:] == ["--check"]:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is not the build of the recordings: run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    if argv[1:]:
        raise SystemExit(__doc__)
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT}: {len(json.loads(text)['variants'])} variants")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
