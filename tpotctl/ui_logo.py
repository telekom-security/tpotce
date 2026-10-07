"""The T-Pot logo and wordmark for the T-Pot scripts: the data in the ui block of installer/lib/ui.sh.

The scripts show the ANSI logo of the T-Pot Manager (splash_art.py) as a still picture, rendered in
bash by fuUI_LOGO_RENDER, and the wordmark of logo.py over their banner. install.sh runs from curl
without Python, so both live as text inside the ui block, in ui.sh and in the same copy in
install.sh, between the marks `# >>> tpot logo data >>>` and `# <<< tpot logo data <<<`:

  python3 -m tpotctl.ui_logo             writes them from splash_art.py and logo.py
  python3 -m tpotctl.ui_logo --check     exit 1 when a copy differs (tests run it too)
  python3 -m tpotctl.ui_logo --fallback  prints the plain fallback of the newer helpers (FALLBACK)

The format: two pixels (top, bottom) make one character, as in the splash. Every pair of pixels the
logo has is an entry of one table (myUI_LOGO_PAIRS, the two digits of the colour indices, 00 first).
A character row is a string of tokens of two symbols of ALPHABET: the entry of the pair and the length
of the run (1-87). Empty cells at the end of a row are left out. The wordmark is two columns per
pixel and half blocks, three text rows.

Standard library only (Python 3.9), it reads logo.py as text: logo.py needs Rich.
"""

import ast
import os
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


# The plain fallback of the newer helpers of ui.sh for the `# >>> plain fallback` block of a script
# (a checkout of an earlier release has no installer/lib/ui.sh): the same plain text as ui.sh without
# gum (tests/test_ui.py compares them). Take the functions the script calls.
FALLBACK = r'''    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
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
      local myALL="" mySELECTED="" myI myN myPICK myPART myA myB myOK
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --filter) shift ;;
          --all) myALL=1; shift ;;
          --selected) mySELECTED="${2:-}"; shift $(( $# < 2 ? $# : 2 )) ;;
          *) break ;;
        esac
      done
      local myHEADER="${1:-}"
      [ "$#" -eq 0 ] || shift
      local -a myLABELS=() myVALUES=() myON=() myNEW=()
      for myI in "$@"; do
        myLABELS+=("${myI%:*}")
        myVALUES+=("${myI##*:}")
        if [ -n "${myALL}" ] || [[ ",${mySELECTED}," == *",${myVALUES[${#myVALUES[@]}-1]},"* ]];
          then myON+=(1)
          else myON+=("")
        fi
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
        then "$@" < /dev/null || myRC=$?
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
        return 1 if wrong else 0
    if argv:
        print("usage: python3 -m tpotctl.ui_logo [--check | --fallback]", file=sys.stderr)
        return 2
    for path in write():
        print(f"wrote {os.path.relpath(path, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
