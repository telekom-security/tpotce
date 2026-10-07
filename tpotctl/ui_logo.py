"""The T-Pot logo and wordmark for the T-Pot scripts: the data in the ui block of installer/lib/ui.sh.

The scripts show the ANSI logo of the T-Pot Manager (splash_art.py) as a still picture, rendered in
bash by fuUI_LOGO_RENDER, and the wordmark of logo.py over their banner. install.sh runs from curl
without Python, so both live as text inside the ui block, in ui.sh and in the same copy in
install.sh, between the marks `# >>> tpot logo data >>>` and `# <<< tpot logo data <<<`:

  python3 -m tpotctl.ui_logo             writes them from splash_art.py and logo.py, and the
                                         plain fallback blocks of the scripts (FALLBACK_FILES)
  python3 -m tpotctl.ui_logo --check     exit 1 when a copy differs (tests run it too)
  python3 -m tpotctl.ui_logo --fallback  prints the plain fallback of the helpers (FALLBACK)

The format: two pixels (top, bottom) make one character, as in the splash. Every pair of pixels the
logo has is an entry of one table (myUI_LOGO_PAIRS, the two digits of the colour indices, 00 first).
A character row is a string of tokens of two symbols of ALPHABET: the entry of the pair and the length
of the run (1-87). Empty cells at the end of a row are left out. The wordmark is two columns per
pixel and half blocks, three text rows.

Standard library only (Python 3.9), it reads logo.py as text: logo.py needs Rich.
"""

import ast
import os
import re
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tpotctl import splash_art

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FILES = (os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(REPO, "install.sh"))
LOGO_PY = os.path.join(HERE, "logo.py")

BEGIN = "# >>> tpot logo data >>>"
END = "# <<< tpot logo data <<<"
VARIANTS = ("120", "80", "80x24")
BUDGET = 9000                        # bytes of the data block, per copy
# printable ASCII without what bash quotes or matches: ' " \ $ ` [ ]  (and no space)
ALPHABET = "".join(chr(c) for c in range(0x21, 0x7f) if chr(c) not in "'\"\\$`[]")
RUN = len(ALPHABET)                  # the longest run of one token

Pair = Tuple[int, int]


def array_name(variant: str) -> str:
    return f"myUI_LOGO_{variant}"


def pairs(variant: str) -> List[List[Pair]]:
    """The character rows of a variant as (top, bottom) pixel pairs."""
    width, height, base = splash_art.grid(variant)
    return [[(base[2 * r * width + x], base[(2 * r + 1) * width + x]) for x in range(width)]
            for r in range(height // 2)]


def pair_table(grids: Optional[Iterable[List[List[Pair]]]] = None) -> List[Pair]:
    """Every pair of the variants once, (0, 0) first, then in the order they come."""
    if grids is None:
        grids = [pairs(variant) for variant in VARIANTS]
    table: List[Pair] = [(0, 0)]
    seen = {(0, 0)}
    for grid in grids:
        for row in grid:
            for pair in row:
                if pair not in seen:
                    seen.add(pair)
                    table.append(pair)
    if len(table) > len(ALPHABET):
        raise ValueError(f"the logo has {len(table)} pairs of pixels, the alphabet only {len(ALPHABET)} "
                         "symbols: the format of the logo data needs a change")
    return table


def encode_row(row: Sequence[Pair], index: Dict[Pair, int]) -> str:
    row = list(row)
    while row and row[-1] == (0, 0):
        row.pop()
    out = []
    x = 0
    while x < len(row):
        end = x
        while end < len(row) and row[end] == row[x] and end - x < RUN:
            end += 1
        out.append(ALPHABET[index[row[x]]] + ALPHABET[end - x - 1])
        x = end
    return "".join(out)


def decode_row(text: str, table: Sequence[Pair], width: int) -> List[Pair]:
    row: List[Pair] = []
    for i in range(0, len(text), 2):
        row.extend([table[ALPHABET.index(text[i])]] * (ALPHABET.index(text[i + 1]) + 1))
    return row + [(0, 0)] * (width - len(row))


def manager_wordmark() -> List[str]:
    """logo.WORDMARK, read from logo.py as text (logo.py needs Rich)."""
    with open(LOGO_PY, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "WORDMARK" for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise ValueError("no WORDMARK in logo.py")


_HALF = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}


def wordmark_lines(rows: Optional[List[str]] = None) -> List[str]:
    """The wordmark for gum: two columns per pixel, two pixel rows per line (half blocks)."""
    rows = list(rows or manager_wordmark())
    if len(rows) % 2:
        rows.append("." * len(rows[0]))
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        lines.append("".join(_HALF[(a == "#", b == "#")] * 2 for a, b in zip(top, bottom)).rstrip())
    return lines


def wordmark_pixels(lines: List[str]) -> List[str]:
    """The pixels of wordmark lines again (for the tests), as rows of # and ."""
    width = max(len(line) for line in lines) // 2 + max(len(line) for line in lines) % 2
    out = []
    for line in lines:
        line = line.ljust(width * 2)
        cells = [line[2 * x] for x in range(width)]
        if any(line[2 * x] != line[2 * x + 1] for x in range(width)):
            raise ValueError(f"a pixel of the wordmark is not two columns wide: {line!r}")
        out.append("".join("#" if c in "▀█" else "." for c in cells))
        out.append("".join("#" if c in "▄█" else "." for c in cells))
    return out


def generate() -> str:
    """The data block, marks included."""
    grids = {variant: pairs(variant) for variant in VARIANTS}
    table = pair_table(grids[v] for v in VARIANTS)
    index = {pair: i for i, pair in enumerate(table)}
    out = [BEGIN,
           "# generated from tpotctl/logo.py and tpotctl/splash_art.py by python3 -m tpotctl.ui_logo, do",
           "# not edit; the format is in tpotctl/ui_logo.py",
           "myUI_WORDMARK=("]
    out += [f"  '{line}'" for line in wordmark_lines()]
    out += [")",
            f"myUI_LOGO_ABC='{ALPHABET}'",
            "myUI_LOGO_PAIRS='" + " ".join(f"{top}{bottom}" for top, bottom in table) + "'"]
    for variant in VARIANTS:
        out.append(f"{array_name(variant)}=(")
        out += [f"'{encode_row(row, index)}'" for row in grids[variant]]
        out.append(")")
    out.append(END)
    return "\n".join(out)


def data_block(text: str) -> str:
    start = text.index(BEGIN)
    return text[start:text.index(END, start) + len(END)]


def bash_strings(block: str, name: str) -> List[str]:
    """The single quoted elements of the bash array name=( ... ) in the block."""
    start = block.index(f"\n{name}=(") + len(name) + 3
    body = block[start:block.index("\n)", start)]
    return [line.strip()[1:-1] for line in body.split("\n") if line.strip()]


def decode(text: str, variant: str) -> List[List[Pair]]:
    """The pixel pairs of a variant from the data block in text (ui.sh or install.sh)."""
    block = data_block(text)
    table_text = block[block.index("myUI_LOGO_PAIRS='") + 17:]
    table = [(int(p[0]), int(p[1])) for p in table_text[:table_text.index("'")].split()]
    width = splash_art.grid(variant)[0]
    return [decode_row(row, table, width) for row in bash_strings(block, array_name(variant))]


def check(files: Sequence[str] = FILES) -> List[str]:
    """The files whose data block is not the generated one."""
    want = generate()
    wrong = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            if data_block(text) != want:
                wrong.append(path)
        except ValueError:
            wrong.append(path)
    return wrong


def write(files: Sequence[str] = FILES) -> List[str]:
    """Writes the data block into the files (between the marks there, which must exist); the
    changed ones. Mode and owner stay, the file is rewritten in place."""
    want = generate()
    if len(want.encode("utf-8")) > BUDGET:
        raise ValueError(f"the logo data has {len(want.encode('utf-8'))} bytes, more than {BUDGET}")
    changed = []
    for path in files:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        new = text.replace(data_block(text), want)
        if new != text:
            with open(path, "w", encoding="utf-8", newline="") as out:
                out.write(new)
            changed.append(path)
    return changed


# The plain fallback of the helpers of ui.sh for the `# >>> plain fallback` block of a script (a
# checkout of an earlier release has no installer/lib/ui.sh): the same plain text as ui.sh with
# TPOT_GUM=off and without a terminal on stdout (tests/test_ui.py compares them; at a terminal ui.sh
# also breaks long lines, the fallback does not). python3 -m tpotctl.ui_logo writes the functions a
# script of FALLBACK_FILES calls into its block, with the ones they call themselves, in this order.
FALLBACK = r'''    fuUI_INIT () { return 0; }
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_INFO () { echo "### $*"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () {
      local myANSWER=""
      while [ "${myANSWER}" != "y" ] && [ "${myANSWER}" != "n" ]; do
        read -rp "### $1 (y/n) " myANSWER || return 1
      done
      [ "${myANSWER}" = "y" ]
    }
    fuUI_CHOOSE () {
      local myHEADER="$1" myI=1 myITEM myPICK
      shift
      echo "### ${myHEADER}" >&2
      for myITEM in "$@"; do
        echo "###   ${myI}) ${myITEM%%:*}" >&2
        myI=$((myI + 1))
      done
      while true; do
        read -rp "### Choice (1-$#): " myPICK || return 1
        if [[ "${myPICK}" =~ ^[0-9]+$ ]] && [ "${myPICK}" -ge 1 ] && [ "${myPICK}" -le "$#" ];
          then
            myITEM="${!myPICK}"
            echo "${myITEM#*:}"
            return 0
        fi
      done
    }
    fuUI_INPUT () {
      local myVALUE=""
      if [ "$2" = "password" ];
        then read -rsp "### $1 " myVALUE; echo >&2
        else read -rp "### $1 " myVALUE
      fi
      echo "${myVALUE}"
    }
    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
    fuMARK () { fuUI_MARKS_ON || return 0; echo "@@tpot $*"; }
    fuUI_LOGO () { return 1; }
    fuUI_VERSION () {
      local myDIR="${1:-${HOME}/tpotce}" myV="" myLINE myRE='^[0-9A-Za-z][0-9A-Za-z._+~-]*$' myENV
      myENV='^[[:space:]]*TPOT_VERSION[[:space:]]*[=:][[:space:]]*["'"'"']?([^"'"'"'[:space:]]*)'
      if [ -n "${myUI_VERSION:-}" ]; then echo "${myUI_VERSION}"; return 0; fi
      [ -r "${myDIR}/version" ] && { IFS= read -r myV < "${myDIR}/version" || true; }
      myV="${myV//[[:space:]]/}"
      [[ "${myV}" =~ ${myRE} ]] || myV=""
      if [ -z "${myV}" ] && [ -r "${myDIR}/.env" ]; then
        while IFS= read -r myLINE || [ -n "${myLINE}" ]; do
          [[ "${myLINE}" =~ ${myENV} ]] && myV="${BASH_REMATCH[1]}"
        done < "${myDIR}/.env"
        [[ "${myV}" =~ ${myRE} ]] || myV=""
      fi
      echo "${myV}"
    }
    fuUI_VERSION_GE () {
      local myI myX myY myN myV="${1//[[:space:]]/}" myM="${2//[[:space:]]/}"
      local -a myA=() myB=()
      myV="${myV#v}"
      myV="${myV%%[-+]*}"
      [ -n "${myV}" ] && [ -n "${myM}" ] || return 1
      IFS=. read -r -a myA <<< "${myV}"
      IFS=. read -r -a myB <<< "${myM}"
      myN="${#myA[@]}"
      [ "${#myB[@]}" -le "${myN}" ] || myN="${#myB[@]}"
      for ((myI = 0; myI < myN; myI++)); do
        myX="${myA[myI]-0}" myY="${myB[myI]-0}"
        [[ "${myX}" =~ ^[0-9]{1,18}$ && "${myY}" =~ ^[0-9]{1,18}$ ]] || return 1
        if [ "$((10#${myX}))" -gt "$((10#${myY}))" ]; then return 0; fi
        if [ "$((10#${myX}))" -lt "$((10#${myY}))" ]; then return 1; fi
      done
      return 0
    }
    fuUI_HELP () {
      local myTITLE="$1" myUSAGE="$2" myW=0 myI myLINE myFIRST myREST myPAD
      shift 2
      local -a myABOUT=() myFLAGS=() myTEXTS=() myCMDS=() myCTEXTS=() myNOTES=()
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --about) myABOUT+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          --opt) myFLAGS+=("${2:-}"); myTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
          --example) myCMDS+=("${2:-}"); myCTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
          --note) myNOTES+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          *) shift ;;
        esac
      done
      for myI in "${!myFLAGS[@]}"; do
        if [ "${#myFLAGS[myI]}" -gt "${myW}" ] && [ "${#myFLAGS[myI]}" -le 24 ]; then myW="${#myFLAGS[myI]}"; fi
      done
      printf 'T-Pot %s\n\nUsage: %s\n' "${myTITLE}" "${myUSAGE//$'\n'/$'\n'       }"
      for myLINE in "${myABOUT[@]}"; do printf '\n%s\n' "${myLINE}"; done
      if [ "${#myFLAGS[@]}" -gt 0 ]; then
        printf '\nOptions:\n'
        printf -v myPAD '%*s' $((myW + 6)) ''
        for myI in "${!myFLAGS[@]}"; do
          myFIRST="${myTEXTS[myI]%%$'\n'*}"
          myREST=""
          [ "${myFIRST}" = "${myTEXTS[myI]}" ] || myREST="${myTEXTS[myI]#*$'\n'}"
          if [ "${#myFLAGS[myI]}" -le "${myW}" ];
            then printf '  %s%*s    %s\n' "${myFLAGS[myI]}" $((myW - ${#myFLAGS[myI]})) '' "${myFIRST}"
            else printf '  %s\n%s%s\n' "${myFLAGS[myI]}" "${myPAD}" "${myFIRST}"
          fi
          [ -z "${myREST}" ] || printf '%s%s\n' "${myPAD}" "${myREST//$'\n'/$'\n'${myPAD}}"
        done
      fi
      if [ "${#myCMDS[@]}" -gt 0 ]; then
        printf '\nExamples:\n'
        for myI in "${!myCMDS[@]}"; do
          printf '  %s\n' "${myCMDS[myI]}"
          [ -z "${myCTEXTS[myI]}" ] || printf '      %s\n' "${myCTEXTS[myI]//$'\n'/$'\n'      }"
        done
      fi
      if [ "${#myNOTES[@]}" -gt 0 ]; then
        printf '\nNotes:\n'
        for myLINE in "${myNOTES[@]}"; do printf '  %s\n' "${myLINE//$'\n'/$'\n'  }"; done
      fi
      return 0
    }
    fuUI_USAGE_ERROR () {
      echo "### [ERROR] - $1" >&2
      echo "###   ${2:-${0##*/}} -h shows the options." >&2
      return 1
    }
    fuUI_RESULT () {
      case "$1" in
        ok) echo "### [OK] - $2" ;;
        fail) echo "### [FAILED] - $2" ;;
        warn) echo "### [WARNING] - $2" ;;
        next) echo "### [NEXT] - $2" ;;
        *) echo "### $2" ;;
      esac
    }
    fuUI_SUMMARY () {
      local myTITLE="$1" myITEM myKIND myTEXT myRC=0
      shift
      echo
      echo "### ${myTITLE}"
      for myITEM in "$@"; do
        myKIND="${myITEM%%:*}" myTEXT="${myITEM#*:}"
        case "${myKIND}" in ok|fail|warn|next|info) ;; *) myKIND="info" myTEXT="${myITEM}" ;; esac
        [ "${myKIND}" != "fail" ] || myRC=1
        fuUI_RESULT "${myKIND}" "${myTEXT}"
      done
      echo
      return "${myRC}"
    }
    fuUI_CHOOSE_MANY () {
      local myALL="" myI myJ myN myPICK myPART myA myB myOK
      local -a mySELECTED=()
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --filter) shift ;;
          --all) myALL=1; shift ;;
          --selected) mySELECTED+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          *) break ;;
        esac
      done
      local myHEADER="${1:-}"
      [ "$#" -eq 0 ] || shift
      local -a myLABELS=() myVALUES=() myON=() myNEW=()
      for myI in "$@"; do
        myLABELS+=("${myI%:*}")
        myVALUES+=("${myI##*:}")
        myON+=("${myALL}")
        for myJ in "${mySELECTED[@]}"; do
          [ "${myJ}" != "${myVALUES[${#myVALUES[@]}-1]}" ] || myON[${#myON[@]}-1]=1
        done
      done
      myN="${#myLABELS[@]}"
      echo "### ${myHEADER}" >&2
      for myI in "${!myLABELS[@]}"; do
        if [ -n "${myON[myI]}" ]; then echo "###   $((myI + 1))) [x] ${myLABELS[myI]}" >&2
        else echo "###   $((myI + 1))) [ ] ${myLABELS[myI]}" >&2; fi
      done
      while true; do
        read -rp "### Choice (i.e. 1,3-5; a = all, n = none, enter = the marked ones): " myPICK || return 1
        myPICK="${myPICK//[[:space:]]/}"
        case "${myPICK}" in
          "") break ;;
          a|A) for myI in "${!myON[@]}"; do myON[myI]=1; done; break ;;
          n|N) for myI in "${!myON[@]}"; do myON[myI]=""; done; break ;;
        esac
        myNEW=()
        myOK=1
        for myI in "${!myON[@]}"; do myNEW[myI]=""; done
        if [[ "${myPICK}" =~ ^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$ ]]; then
          for myPART in ${myPICK//,/ }; do
            myA=$((10#${myPART%-*})) myB=$((10#${myPART#*-}))
            if [ "${myA}" -lt 1 ] || [ "${myB}" -gt "${myN}" ] || [ "${myA}" -gt "${myB}" ]; then myOK=""; break; fi
            for ((myI = myA; myI <= myB; myI++)); do myNEW[myI - 1]=1; done
          done
        else myOK=""
        fi
        if [ -n "${myOK}" ]; then myON=("${myNEW[@]}"); break; fi
        echo "### [WARNING] - Not a choice: ${myPICK}" >&2
      done
      for myI in "${!myVALUES[@]}"; do
        [ -z "${myON[myI]}" ] || printf '%s\n' "${myVALUES[myI]}"
      done
      return 0
    }
    fuUI_SPIN () {
      local myTITLE="$1" myLOG="$2" myRC=0
      shift 2
      echo "### ${myTITLE}"
      if fuUI_MARKS_ON;
        then { "$@" < /dev/null 2>&1 | tee -a "${myLOG}" 2>/dev/null; myRC="${PIPESTATUS[0]}"; } || true
        else "$@" >>"${myLOG}" 2>&1 < /dev/null || myRC=$?
      fi
      if [ "${myRC}" -eq 0 ];
        then echo "### [OK] - ${myTITLE%% ...}"
        else
          if fuUI_MARKS_ON;
            then echo "### [ERROR] - ${myTITLE%% ...} failed" >&2
            else echo "### [ERROR] - ${myTITLE%% ...} failed, the end of ${myLOG}:" >&2; tail -n 15 "${myLOG}" >&2
          fi
      fi
      return "${myRC}"
    }
'''


FALLBACK_BEGIN = "# >>> plain fallback"
FALLBACK_END = "# <<< plain fallback"


def _fallback_files() -> Tuple[str, ...]:
    """The scripts at the top of the checkout and in the folders of docker/ with a fallback block
    (update.sh, restore.sh, genuser.sh, deploy.sh and the image builder; tests/test_ui.py names them).
    Found by the mark, so a new script gets its block from the generator too."""
    found = []
    docker = os.path.join(REPO, "docker")
    folders = [REPO] + sorted(os.path.join(docker, name) for name in
                              (os.listdir(docker) if os.path.isdir(docker) else [])
                              if os.path.isdir(os.path.join(docker, name)))
    for folder in folders:
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if name.endswith(".sh") and os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as handle:
                    if "\n" + FALLBACK_BEGIN in handle.read():
                        found.append(path)
    return tuple(found)


# the scripts whose fallback block is generated from FALLBACK
FALLBACK_FILES = _fallback_files()
# the scripts with a fallback block of their own (deeper than the folders of docker/), and why
FALLBACK_EXCLUDED = {
    "docker/tpotinit/dist/bin/hptest.sh":
        "runs inside the tpotinit image too, which has no installer/lib/ui.sh: its small block is its "
        "look there (fuMARK of TPOT_MARKS only, no checkout of an earlier release)",
    "docker/tpotinit/dist/bin/attackmap_pipeline_test.sh":
        "runs inside the tpotinit image too, which has no installer/lib/ui.sh: its small block is its "
        "look there (fuMARK of TPOT_MARKS only, no checkout of an earlier release)",
}
_HELPER = re.compile(r"\b(fuUI_\w+|fuMARK)\b")
_DEFINED = re.compile(r"^\s*(fu\w+)\s*\(\)", re.M)


def fallback_functions(text: str = FALLBACK) -> "Dict[str, str]":
    """The functions of FALLBACK (or of a fallback block) by name, in their order: a line of four
    spaces and `fuNAME () {` with the body on the same line, or up to the line `    }`."""
    out: Dict[str, str] = {}
    name: Optional[str] = None
    lines: List[str] = []
    for line in text.split("\n"):
        match = re.match(r"    (fu\w+) \(\) \{(.*)$", line)
        if name is None and match:
            if match.group(2).strip():
                out[match.group(1)] = line
            else:
                name, lines = match.group(1), [line]
        elif name is not None:
            lines.append(line)
            if line == "    }":
                out[name] = "\n".join(lines)
                name = None
    if name is not None:
        raise ValueError(f"{name} of the fallback has no end (a line of four spaces and }})")
    return out


def _fallback_span(text: str) -> Tuple[int, int]:
    """Where the body of the fallback block is: after the line of its begin mark up to its end mark."""
    start = text.index("\n" + FALLBACK_BEGIN) + 1
    body = text.index("\n", start) + 1
    end = text.index("\n" + FALLBACK_END, start) + 1
    return body, end


def fallback_block(text: str, ui_text: Optional[str] = None) -> str:
    """The body of the fallback block for a script: every function of FALLBACK the script calls
    outside the block and does not define itself, with the ones they call, in the order of FALLBACK.
    ValueError for a helper of ui.sh the script calls that FALLBACK does not have."""
    if ui_text is None:
        with open(FILES[0], encoding="utf-8") as handle:
            ui_text = handle.read()
    body, end = _fallback_span(text)
    rest = text[:body] + text[end:]
    canonical = fallback_functions()
    own = set(_DEFINED.findall(rest))
    called = set(_HELPER.findall(rest)) - own
    missing = sorted((called & set(_DEFINED.findall(ui_text))) - set(canonical))
    if missing:
        raise ValueError(f"{', '.join(missing)} of installer/lib/ui.sh {'is' if len(missing) == 1 else 'are'} "
                         "not in ui_logo.FALLBACK: add the plain form there")
    want = called & set(canonical)
    todo = list(want)
    while todo:
        for name in _HELPER.findall(canonical[todo.pop()].split("{", 1)[1]):
            if name in canonical and name not in want:
                want.add(name)
                todo.append(name)
    return "".join(canonical[name] + "\n" for name in canonical if name in want)


def check_fallback(files: Sequence[str] = FALLBACK_FILES) -> List[str]:
    """The scripts whose fallback block is not the generated one."""
    wrong = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            body, end = _fallback_span(text)
            if text[body:end] != fallback_block(text):
                wrong.append(path)
        except ValueError:
            wrong.append(path)
    return wrong


def write_fallback(files: Sequence[str] = FALLBACK_FILES) -> List[str]:
    """Writes the generated fallback block into the scripts (in place: mode and owner stay); the
    changed ones."""
    changed = []
    for path in files:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        body, end = _fallback_span(text)
        new = text[:body] + fallback_block(text) + text[end:]
        if new != text:
            with open(path, "w", encoding="utf-8", newline="") as out:
                out.write(new)
            changed.append(path)
    return changed


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--fallback"]:
        sys.stdout.write(FALLBACK)
        return 0
    if argv == ["--check"]:
        wrong = check()
        for path in wrong:
            print(f"the logo data of {os.path.relpath(path, REPO)} is not the generated one, "
                  "run python3 -m tpotctl.ui_logo", file=sys.stderr)
        stale = check_fallback()
        for path in stale:
            print(f"the plain fallback of {os.path.relpath(path, REPO)} is not the generated one, "
                  "run python3 -m tpotctl.ui_logo", file=sys.stderr)
        return 1 if wrong or stale else 0
    if argv:
        print("usage: python3 -m tpotctl.ui_logo [--check | --fallback]", file=sys.stderr)
        return 2
    try:
        changed = write() + write_fallback()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    for path in changed:
        print(f"wrote {os.path.relpath(path, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
