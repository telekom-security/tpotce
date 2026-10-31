#!/usr/bin/env bash
# The T-Pot ISO (a preview): the Debian netinst T-Pot prefers as its host, with the T-Pot installer.
# Its boot menus get "Install T-Pot" (the default) in front of Debian's own entries; the Debian
# installer then asks for the disks, the user and its password as always (preseed.cfg answers only
# what is safe on any machine, it erases no disk on its own), and at the first login of that user
# the install.sh of the ISO installs T-Pot from the repository and the branch the ISO was built for
# (late.sh, profile.sh, start.sh): the same installer as on a prepared Debian.
#
# The netinst is Debian's own, checked against its SHA512SUMS signed by the Debian CD signing key
# (the fingerprint is pinned below). Its kernels, initrds and signed boot loaders (Secure Boot) stay
# untouched, xorriso replays its boot records. Linux with xorriso, python3, gpg and curl, or Docker
# (-D; on macOS and without those tools on its own). A tool for building the ISO, not part of the
# T-Pot Manager.
#
# With no option at a terminal it asks (a menu): build an ISO, write one to a USB stick, build the
# ISOs of a release with their checksums (SHA256SUMS, SHA512SUMS, signed with GPG if wanted, uploaded
# to a GitHub release if wanted). With any option or without a terminal it never asks; -h shows the
# options and the exit codes. Each run asks Debian for its signed SHA512SUMS: a new point release
# replaces the netinst in the cache. It writes only to USB sticks and SD cards, never to the disk of
# the system, of the home folder or of the ISOs.

# the menus need bash 4 (ui.sh stops a step with mapfile): macOS has bash 3.2 as /bin/bash, the one
# of Homebrew when there is one. Sourced (the tests) it stays in the bash it is in
if [ "${BASH_VERSINFO[0]}" -lt 4 ] && [[ "${BASH_SOURCE[0]}" == "$0" ]] && [ -z "${TPOT_ISO_REEXEC:-}" ];
  then
    for myBASH in /opt/homebrew/bin/bash /usr/local/bin/bash; do
      if [ -x "${myBASH}" ] && [ "$("${myBASH}" -c 'echo "${BASH_VERSINFO[0]}"' 2>/dev/null)" -ge 4 ] 2>/dev/null;
        then TPOT_ISO_REEXEC=1 exec "${myBASH}" "$0" "$@"
      fi
    done
fi

# CDPATH="": with an exported CDPATH cd prints the folder it found there, into the path
myDIR="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
myREPO="$(CDPATH="" cd -- "${myDIR}/../.." && pwd)"
# the builder by its full path, for the command lines it prints: they run from anywhere
mySELF="${myDIR}/build.sh"
# the look of the T-Pot scripts; the ISO is built from a checkout, which always has it
# shellcheck source=../lib/ui.sh
if ! source "${myREPO}/installer/lib/ui.sh" 2>/dev/null;
  then
    echo "### [ERROR] - ${myREPO}/installer/lib/ui.sh is missing, build from a complete checkout." >&2
    exit 1
fi

# The Debian of the T-Pot installer (install.sh: mySUPPORTED_VERSION of Debian), the test checks it
myDEBIAN_MAJOR="13"
myDEBIAN_CD="https://cdimage.debian.org"
# The Debian CD signing key (https://www.debian.org/CD/verify): its signature on SHA512SUMS is the
# trust of the netinst
myDEBIAN_KEY="DF9B9C49EAA9298432589D76DA87E80D6294BE9B"
myDEBIAN_KEY_URL="https://www.debian.org/CD/key-DA87E80D6294BE9B.txt"
myUPSTREAM="https://github.com/telekom-security/tpotce"
# how a T-Pot installed from a tag or a commit gets back onto a branch update.sh can follow
myUPDATE_BACK="later on a branch again: ./update.sh -y -b master"
# the Docker image of the build: Debian with the tools of the build, refreshed weekly
myDOCKER_IMAGE="tpot-iso-build:${myDEBIAN_MAJOR}"
# the files of the ISO in /tpot (late.sh puts them into the new system), iso.conf is made here
myPAYLOAD="preseed.cfg late.sh profile.sh start.sh tpot.issue"
# gum for macOS (ui.sh fetches the one of Linux only): the version of ui.sh (the test checks it),
# the sha256 of the release archives of github.com/charmbracelet/gum
myGUM_DARWIN_VERSION="2.0.2"
myGUM_DARWIN_SHA256_arm64="4777a69b1170b8db23c95d5889fb32186cfda1a3ac950d339aa17e3513633890"
myGUM_DARWIN_SHA256_x86_64="5374966c7c7199ea879fcaa525ddc6d447a098d3d35496e430a9a1ef38d30485"
# a drive larger than this is rarely a USB stick: the confirmation says so
myBIG_STICK=128000000000

# the options; the menu sets the same
myACTION=""
myARCH=""
myBRANCH="${TPOT_BRANCH:-}"
myREPO_URL="${TPOT_REPO_URL:-}"
myINPUT=""
myOUT=""
myOUT_GIVEN=""
myDOCKER=""
myYES=""
myRELEASE=""
mySIGN_KEY=""
myUPLOAD_TAG=""
myWRITE_ISO=""
myDEVICE=""
# the downloads, kept between the runs whatever -o says
myCACHE="${TPOT_ISO_CACHE:-${myDIR}/out/cache}"
# the state of a run
myDEF_BRANCH=""
myDEF_REPO_URL=""
myVERSION=""
myPREFIX=""
mySIGN_FPR=""
myGH_REPO=""
myHOW=""
myLOG=""
myWORK=""
myMENU=""
myDOCKER_USED=""
myWROTE_LOG=""
myPEEK_FILE=""
myARCHS=()
myBUILT=()

fuHELP () {
  fuUI_HELP "ISO Builder (preview)" "installer/iso/build.sh [options]" \
    --about "Builds the T-Pot ISO: the Debian ${myDEBIAN_MAJOR} netinst with \"Install T-Pot\" as the default of its boot menus. The Debian installer asks for the disks, the user and its password as always; at the first login of that user the T-Pot installer of the ISO starts. Debian's own entries stay and install a plain Debian." \
    --about "With no option at a terminal it asks: build an ISO, write one to a USB stick, build the ISOs of a release. The netinst is checked against Debian's signed SHA512SUMS, its boot loaders stay untouched (Secure Boot). Linux with xorriso, python3, gpg and curl, or Docker (macOS)." \
    --opt "-a <arch>" "amd64, arm64 or both (default: the architecture of this machine, with -R both)" \
    --opt "-b <branch>" "the branch the ISO installs T-Pot from (default: TPOT_BRANCH, the branch of this checkout, master); a tag or a commit works too, but update.sh cannot update such a T-Pot. At its first login the ISO offers another one as well" \
    --opt "-r <repo-url>" "the repository the ISO installs T-Pot from (default: TPOT_REPO_URL, the origin of this checkout, ${myUPSTREAM})" \
    --opt "-i <netinst>" "a Debian netinst on disk instead of the download (one architecture), checked against Debian's signed SHA512SUMS all the same" \
    --opt "-o <folder>" "where the ISOs go (default: installer/iso/out, with -R installer/iso/out/release-<version>); the downloads stay in installer/iso/out/cache (TPOT_ISO_CACHE)" \
    --opt "-D" "build in Docker (default outside Linux and without the tools of the build)" \
    --opt "-R" "the ISOs of a release (maintainers): the checkout must be clean and its branch pushed; tpot-<version>-debian-<x.y.z>-<arch>.iso with SHA256SUMS and SHA512SUMS" \
    --opt "-s <gpg-key>" "with -R: signs SHA256SUMS and SHA512SUMS (.asc) with this key" \
    --opt "-U <tag>" "with -R: uploads the files to the GitHub release of this tag (gh, the repository of -r)" \
    --opt "-d <device>" "after the build (one architecture) the ISO goes onto this USB stick; with -w the stick to write to" \
    --opt "-w <iso>" "writes an ISO to a USB stick (-d, at a terminal it asks); only -d and -y go with it" \
    --opt "-l" "lists the USB sticks and SD cards it may write to" \
    --opt "-y" "never asks: confirms erasing the stick of -d and replacing the files of a release" \
    --opt "-h" "this help" \
    --example "installer/iso/build.sh" "at a terminal: the menu" \
    --example "installer/iso/build.sh -a amd64 -b master" "the ISO for x86-64 that installs the master branch" \
    --example "installer/iso/build.sh -a amd64 -b my-fix -r https://github.com/<you>/tpotce" "an ISO to test a branch of a fork" \
    --example "installer/iso/build.sh -a amd64 -d /dev/sdb" "builds it and writes it to the USB stick /dev/sdb (it asks first)" \
    --example "installer/iso/build.sh -w installer/iso/out/tpot-master-debian-${myDEBIAN_MAJOR}.x.y-amd64.iso" "writes an ISO, the stick is picked from a list" \
    --example "installer/iso/build.sh -R -s <gpg-key> -U <tag>" "the ISOs of a release for both architectures, signed and uploaded" \
    --note "The ISO asks before it installs anything and never erases a disk on its own. It holds no password or key." \
    --note "USB sticks: only removable or USB drives are listed and written to, never the disk of the system, of the home folder or of the ISOs; the stick is read back and checked. Windows: Rufus (DD mode) or balenaEtcher." \
    --note "Each run asks Debian for its signed SHA512SUMS: a new point release replaces the netinst in the cache, a broken download is fetched again. The Docker image is refreshed weekly." \
    --note "Exit codes: 0 done, 1 failed, 2 wrong options, 130 cancelled."
}

fuWORK_CLEAN () {
  # xorriso extracts read-only files and folders
  if [ -n "${myWORK}" ] && [ -d "${myWORK}" ];
    then
      chmod -R u+w "${myWORK}" 2>/dev/null
      rm -rf "${myWORK}"
  fi
  myWORK=""
}

fuNORMALIZE_REPO () {
  # a repository URL without a trailing slash or .git (install.sh: normalize_repo); GitHub over SSH
  # becomes HTTPS, a T-Pot host has no key of GitHub
  local myURL="${1%/}"
  myURL="${myURL%.git}"
  case "${myURL}" in
    git@github.com:*) myURL="https://github.com/${myURL#git@github.com:}" ;;
    ssh://git@github.com/*) myURL="https://github.com/${myURL#ssh://git@github.com/}" ;;
  esac
  echo "${myURL}"
}

fuVALID_REPO () {
  # what start.sh of the ISO accepts as the repository (the test compares the two)
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._~:/@%+=-]*$ ]]
}

fuVALID_BRANCH () {
  # what start.sh of the ISO accepts as the branch, tag or commit
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/+-]*$ ]]
}

fuGIT () {
  command -v git >/dev/null && git -C "${myREPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    && git -C "${myREPO}" "$@" 2>/dev/null
}

fuSOURCE () {
  # -b and -r win, then the environment, then this checkout (myDEF_*), then master of upstream
  # (install.sh: resolve_tpot_source); a detached HEAD gives its commit. rc 1 for one start.sh of
  # the ISO would not take
  myDEF_BRANCH=$(fuGIT rev-parse --abbrev-ref HEAD)
  [ "${myDEF_BRANCH}" != "HEAD" ] || myDEF_BRANCH=$(fuGIT rev-parse HEAD)
  [ -n "${myDEF_BRANCH}" ] || myDEF_BRANCH="master"
  myDEF_REPO_URL=$(fuGIT remote get-url origin)
  [ -n "${myDEF_REPO_URL}" ] || myDEF_REPO_URL="${myUPSTREAM}"
  myDEF_REPO_URL=$(fuNORMALIZE_REPO "${myDEF_REPO_URL}")
  [ -n "${myBRANCH}" ] || myBRANCH="${myDEF_BRANCH}"
  [ -n "${myREPO_URL}" ] || myREPO_URL="${myDEF_REPO_URL}"
  myREPO_URL=$(fuNORMALIZE_REPO "${myREPO_URL}")
  fuVALID_REPO "${myREPO_URL}" || { fuUI_USAGE_ERROR "Not a repository URL: ${myREPO_URL}" "${mySELF##*/}"; return 1; }
  fuVALID_BRANCH "${myBRANCH}" || { fuUI_USAGE_ERROR "Not a branch, tag or commit: ${myBRANCH}" "${mySELF##*/}"; return 1; }
}

fuTIMEOUT () {
  # fuTIMEOUT <seconds> <command> ...: macOS has no timeout, the command runs without one there
  local mySECONDS="$1"
  shift
  if command -v timeout >/dev/null; then timeout "${mySECONDS}" "$@"; else "$@"; fi
}

fuREMOTE () {
  # what the repository has of the branch, into myREMOTE (ok, tag, commit, missing, unreachable,
  # differs, nogit) and myREMOTE_TEXT; the installer of the ISO clones it at the first login. tag and
  # commit: the installer checks it out, update.sh of that T-Pot refuses the detached HEAD. differs:
  # the branch of this checkout, the repository has another commit (the ISO carries install.sh of
  # this checkout). myREMOTE_COMMIT: the commit of the branch or tag there
  local myLS myRC=0 myHEAD myREF
  myREMOTE="" myREMOTE_TEXT="" myREMOTE_COMMIT=""
  if ! command -v git >/dev/null;
    then myREMOTE="nogit" myREMOTE_TEXT="git is missing here: whether ${myREPO_URL} has ${myBRANCH} is not checked"; return 0
  fi
  if [[ "${myBRANCH}" =~ ^[0-9a-f]{40}$ ]];
    then myREMOTE="commit" myREMOTE_TEXT="${myBRANCH:0:7} is a commit: the installer checks it out, update.sh cannot update it (${myUPDATE_BACK})"; return 0
  fi
  myLS=$(GIT_TERMINAL_PROMPT=0 fuTIMEOUT 30 git ls-remote --exit-code "${myREPO_URL}" "refs/heads/${myBRANCH}" \
         "refs/tags/${myBRANCH}" "refs/tags/${myBRANCH}^{}" 2>/dev/null) || myRC=$?
  if [ "${myRC}" -eq 2 ];
    then myREMOTE="missing" myREMOTE_TEXT="${myREPO_URL} has no branch or tag ${myBRANCH}: the installer of the ISO could not get T-Pot"; return 0
  elif [ "${myRC}" -ne 0 ];
    then myREMOTE="unreachable" myREMOTE_TEXT="${myREPO_URL} cannot be reached now: whether it has ${myBRANCH} is not checked"; return 0
  fi
  # a branch first, then the commit of an annotated tag (^{}), then the tag itself
  myREF=$(awk -v b="${myBRANCH}" '
    $2 == "refs/heads/" b { head = $1 } $2 == "refs/tags/" b "^{}" { peeled = $1 } $2 == "refs/tags/" b { tag = $1 }
    END { print (head != "" ? "head " head : (peeled != "" ? "tag " peeled : "tag " tag)) }' <<< "${myLS}")
  myREMOTE_COMMIT="${myREF#* }"
  if [ "${myREF%% *}" = "tag" ];
    then
      myREMOTE="tag"
      myREMOTE_TEXT="${myBRANCH} is a tag of ${myREPO_URL} (${myREMOTE_COMMIT:0:7}): the installer checks it out, update.sh cannot update it (${myUPDATE_BACK})"
      return 0
  fi
  myREMOTE="ok" myREMOTE_TEXT="${myREPO_URL} has ${myBRANCH} (${myREMOTE_COMMIT:0:7})"
  myHEAD=$(fuGIT rev-parse HEAD)
  if [ -n "${myHEAD}" ] && [ "${myBRANCH}" = "${myDEF_BRANCH}" ] && [ "${myREPO_URL}" = "${myDEF_REPO_URL}" ] \
     && [ "${myREMOTE_COMMIT}" != "${myHEAD}" ];
    then
      myREMOTE="differs"
      myREMOTE_TEXT="${myREPO_URL} has ${myBRANCH} at ${myREMOTE_COMMIT:0:7}, this checkout is at ${myHEAD:0:7}: the ISO carries install.sh of this checkout, its installer clones the one of the repository (git push or git pull)"
  fi
  return 0
}

fuCHECK_REMOTE () {
  # the warning of fuREMOTE when the repository may not give the installer of the ISO what it needs,
  # or what it gives cannot be updated (a tag or a commit)
  fuREMOTE
  [ "${myREMOTE}" = "ok" ] || fuUI_WARN "${myREMOTE_TEXT}."
}

fuHASH () {
  # fuHASH <256|512> <file|->: the sha256 or sha512 in hex (sha256sum / sha512sum, shasum of macOS)
  local myHASH
  myHASH=$({ if command -v "sha${1}sum" >/dev/null 2>&1; then "sha${1}sum" "$2"; else shasum -a "$1" "$2"; fi; } 2>/dev/null \
           | awk '{ print $1; exit }')
  [[ "${myHASH}" =~ ^[0-9a-f]{64}([0-9a-f]{64})?$ ]] || return 1
  echo "${myHASH}"
}

fuSIZE () {
  # fuSIZE <file>: its size in bytes
  local mySIZE
  mySIZE=$(wc -c < "$1" 2>/dev/null) || return 1
  echo "${mySIZE// /}"
}

fuHUMAN () {
  # fuHUMAN <bytes>: 745 MB, 32.0 GB (powers of 1000, as the sticks are sold)
  awk -v b="$1" 'BEGIN { if (b >= 1e9) printf "%.1f GB\n", b / 1e9; else printf "%d MB\n", b / 1e6 }'
}

fuHOST_ARCH () {
  case "$(uname -m 2>/dev/null)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
  esac
}

fuCOMMIT () {
  # the commit of this checkout, +changes when it has some
  local myCOMMIT
  if [ -n "${TPOT_ISO_SOURCE_COMMIT:-}" ]; then echo "${TPOT_ISO_SOURCE_COMMIT}"; return; fi
  myCOMMIT=$(fuGIT rev-parse HEAD) || { echo "unknown"; return; }
  [ -z "$(fuGIT status --porcelain)" ] || myCOMMIT="${myCOMMIT}+changes"
  echo "${myCOMMIT}"
}

fuGUM_MACOS () {
  # macOS: ui.sh fetches gum for Linux only; the menus get the gum release of macOS (pinned, its
  # sha256 checked) in the same place, fuUI_INIT takes it from there. Without it plain text
  local myBIN="${XDG_DATA_HOME:-${HOME}/.local/share}/tpotce/bin" myARCH mySHA myNAME myTMP
  [ "$(uname -s 2>/dev/null)" = "Darwin" ] && [ -t 1 ] && [ "${TPOT_GUM:-on}" != "off" ] || return 0
  if [ -x "${myBIN}/gum" ] && "${myBIN}/gum" --version 2>/dev/null | grep -q "${myGUM_DARWIN_VERSION}"; then return 0; fi
  case "$(uname -m 2>/dev/null)" in
    arm64) myARCH="arm64" mySHA="${myGUM_DARWIN_SHA256_arm64}" ;;
    x86_64) myARCH="x86_64" mySHA="${myGUM_DARWIN_SHA256_x86_64}" ;;
    *) return 0 ;;
  esac
  myNAME="gum_${myGUM_DARWIN_VERSION}_Darwin_${myARCH}"
  myTMP=$(mktemp -d "${TMPDIR:-/tmp}/tpot-gum.XXXXXX" 2>/dev/null) || return 0
  if curl -fsSL --max-time 60 -o "${myTMP}/gum.tgz" \
       "https://github.com/charmbracelet/gum/releases/download/v${myGUM_DARWIN_VERSION}/${myNAME}.tar.gz" 2>/dev/null \
     && [ "$(fuHASH 256 "${myTMP}/gum.tgz")" = "${mySHA}" ] \
     && tar xzf "${myTMP}/gum.tgz" -C "${myTMP}" "${myNAME}/gum" 2>/dev/null \
     && mkdir -p "${myBIN}" && install -m 0755 "${myTMP}/${myNAME}/gum" "${myBIN}/gum" 2>/dev/null;
    then :
  fi
  rm -rf "${myTMP}"
  return 0
}

fuFETCH () {
  # fuFETCH <url> <file>
  curl -fsSL --retry 3 --connect-timeout 20 -o "$2" "$1"
}

fuSUMS () {
  # fuSUMS <url of the folder> <folder>: its SHA512SUMS, checked against the signature of the Debian
  # CD signing key (VALIDSIG of the pinned fingerprint, the primary key or the key itself); the key
  # as a keyring of its own, no import: gpg needs no gpg-agent and no trust database for it
  local myGNUPG myRC=0
  mkdir -p "$2"
  fuFETCH "$1/SHA512SUMS" "$2/SHA512SUMS" && fuFETCH "$1/SHA512SUMS.sign" "$2/SHA512SUMS.sign" || return 1
  myGNUPG=$(mktemp -d "${TMPDIR:-/tmp}/tpot-gpg.XXXXXX") || return 1
  chmod 700 "${myGNUPG}"
  if gpg --batch --dearmor < "${myWORK}/debian-cd.key" > "${myGNUPG}/debian-cd.gpg" 2>/dev/null \
     && gpg --homedir "${myGNUPG}" --batch --no-autostart --no-default-keyring \
          --keyring "${myGNUPG}/debian-cd.gpg" --trust-model always --status-fd 1 \
          --verify "$2/SHA512SUMS.sign" "$2/SHA512SUMS" 2>/dev/null \
       | awk -v fpr="${myDEBIAN_KEY}" '$2 == "VALIDSIG" && ($3 == fpr || $NF == fpr) { ok = 1 } END { exit !ok }';
    then
      echo "$1/SHA512SUMS: signed by ${myDEBIAN_KEY}"
    else
      echo "$1/SHA512SUMS: no valid signature of ${myDEBIAN_KEY}" >&2
      myRC=1
  fi
  rm -rf "${myGNUPG}"
  return "${myRC}"
}

fuSUM_OF () {
  # fuSUM_OF <SHA512SUMS> <name>: its sha512
  awk -v name="$2" '$2 == name { print $1; exit }' "$1"
}

fuDOWNLOAD () {
  # fuDOWNLOAD <url> <file> <sha512>: the file, a download cut off before (.part) goes on where it
  # stopped; one that does not match then (the file changed at Debian) is fetched anew once. A new
  # download cut off keeps its .part for the next run
  local myPART="$2.part" myTRY
  for myTRY in resume new; do
    if [ "${myTRY}" = "resume" ];
      then
        [ -s "${myPART}" ] || continue
        echo "going on with ${myPART}"
        curl -fsSL --retry 3 --connect-timeout 20 -C - -o "${myPART}" "$1" || { rm -f "${myPART}"; continue; }
      else
        rm -f "${myPART}"
        echo "downloading $1"
        fuFETCH "$1" "${myPART}" || return 1
    fi
    if [ "$(fuHASH 512 "${myPART}")" = "$3" ]; then mv -f "${myPART}" "$2"; return; fi
    echo "${myPART}: not the sha512 of Debian's SHA512SUMS" >&2
    rm -f "${myPART}"
  done
  return 1
}

fuNETINST () {
  # fuNETINST <arch>: the netinst and its sha512 into ${myWORK}/netinst (path, name, sha512, url of
  # SHA512SUMS, a note of the cache). Debian's signed SHA512SUMS of now each time: the cache keeps
  # the netinst they name, a new point release replaces the old one there, a cached file that does
  # not match is fetched again
  local myA="$1" myNAME myURL mySUM myVER myFILE myF myNOTE="" myOLD=""
  myURL="${myDEBIAN_CD}/debian-cd/current/${myA}/iso-cd"
  fuFETCH "${myDEBIAN_KEY_URL}" "${myWORK}/debian-cd.key" || { echo "cannot get ${myDEBIAN_KEY_URL}" >&2; return 1; }
  fuSUMS "${myURL}" "${myWORK}/current" || return 1
  if [ -n "${myINPUT}" ];
    then
      myNAME="$(basename -- "${myINPUT}")"
      myFILE="${myINPUT}"
      mySUM=$(fuSUM_OF "${myWORK}/current/SHA512SUMS" "${myNAME}")
      if [ -z "${mySUM}" ];
        then
          # an earlier point release: the archive of Debian has its SHA512SUMS
          myVER="${myNAME#debian-}"
          myVER="${myVER%%-*}"
          myURL="${myDEBIAN_CD}/cdimage/archive/${myVER}/${myA}/iso-cd"
          fuSUMS "${myURL}" "${myWORK}/archive" || return 1
          mySUM=$(fuSUM_OF "${myWORK}/archive/SHA512SUMS" "${myNAME}")
      fi
      [ -n "${mySUM}" ] || { echo "${myNAME} is not in Debian's SHA512SUMS (${myURL})" >&2; return 1; }
      myNOTE="Debian netinst: ${myINPUT}"
    else
      myNAME=$(awk -v arch="${myA}" '$2 ~ ("^debian-[0-9.]+-" arch "-netinst\\.iso$") { print $2 }' "${myWORK}/current/SHA512SUMS")
      [ "$(printf '%s\n' "${myNAME}" | grep -c .)" = "1" ] || { echo "no single netinst in ${myURL}/SHA512SUMS" >&2; return 1; }
      mySUM=$(fuSUM_OF "${myWORK}/current/SHA512SUMS" "${myNAME}")
      myFILE="${myCACHE}/${myNAME}"
      mkdir -p "${myCACHE}" || return 1
      for myF in "${myCACHE}"/debian-*-"${myA}"-netinst.iso; do
        [ ! -f "${myF}" ] || [ "${myF}" = "${myFILE}" ] || myOLD="${myOLD:+${myOLD}, }${myF##*/}"
      done
      if [ -f "${myFILE}" ] && [ "$(fuHASH 512 "${myFILE}")" = "${mySUM}" ];
        then
          echo "${myFILE}: in the cache"
          myNOTE="Debian netinst ${myNAME}: up to date in the cache"
        else
          if [ -f "${myFILE}" ];
            then
              myNOTE="Debian netinst ${myNAME}: the cached one did not match Debian's SHA512SUMS, downloaded again"
              rm -f "${myFILE}"
          elif [ -n "${myOLD}" ];
            then myNOTE="New Debian netinst ${myNAME}: downloaded, replaces ${myOLD} in the cache"
            else myNOTE="Debian netinst ${myNAME}: downloaded into the cache"
          fi
          fuDOWNLOAD "${myURL}/${myNAME}" "${myFILE}" "${mySUM}" || return 1
      fi
  fi
  if [ "$(fuHASH 512 "${myFILE}")" != "${mySUM}" ];
    then
      echo "${myFILE}: its sha512 is not the one of Debian's SHA512SUMS (${mySUM})" >&2
      return 1
  fi
  echo "${myFILE}: sha512 matches Debian's SHA512SUMS"
  # the cache keeps the netinst of now: older point releases and downloads cut off go
  if [ -z "${myINPUT}" ];
    then
      for myF in "${myCACHE}"/debian-*-"${myA}"-netinst.iso "${myCACHE}"/debian-*-"${myA}"-netinst.iso.part; do
        [ ! -f "${myF}" ] || [ "${myF}" = "${myFILE}" ] || { echo "removing ${myF} from the cache"; rm -f "${myF}"; }
      done
  fi
  printf '%s\n%s\n%s\n%s\n%s\n' "${myFILE}" "${myNAME}" "${mySUM}" "${myURL}/SHA512SUMS" "${myNOTE}" > "${myWORK}/netinst"
}

fuBUILD () {
  # fuBUILD <arch> <netinst>: extract the boot files, patch them, add /tpot and write the ISO
  # (${myWORK}/new.iso)
  local myA="$1" mySRC="$2" myFILE myREL
  local -a myEXTRACT=(-extract /boot/grub/grub.cfg "${myWORK}/orig/boot/grub/grub.cfg"
                      -extract /md5sum.txt "${myWORK}/orig/md5sum.txt")
  local -a myMAP=() myPATHS=(/tpot)
  if [ "${myA}" = "amd64" ];
    then
      myEXTRACT+=(-extract /boot/grub/theme "${myWORK}/orig/boot/grub/theme")
      for myFILE in menu.cfg txt.cfg gtk.cfg stdmenu.cfg; do
        myEXTRACT+=(-extract "/isolinux/${myFILE}" "${myWORK}/orig/isolinux/${myFILE}")
      done
  fi
  mkdir -p "${myWORK}/orig/boot/grub" "${myWORK}/orig/isolinux" "${myWORK}/overlay/tpot"
  xorriso -osirrox on -indev "${mySRC}" "${myEXTRACT[@]}" || return 1
  PYTHONDONTWRITEBYTECODE=1 python3 "${myDIR}/isotree.py" patch --arch "${myA}" \
    --orig "${myWORK}/orig" --out "${myWORK}/overlay" || return 1
  for myFILE in ${myPAYLOAD}; do
    cp "${myDIR}/${myFILE}" "${myWORK}/overlay/tpot/${myFILE}" || return 1
  done
  cp "${myREPO}/install.sh" "${myREPO}/version" "${myWORK}/overlay/tpot/" || return 1
  printf '%s\n' "# The T-Pot ISO: where its T-Pot installer gets T-Pot from (start.sh reads it, never sources it)" \
    "TPOT_REPO_URL=${myREPO_URL}" "TPOT_BRANCH=${myBRANCH}" > "${myWORK}/overlay/tpot/iso.conf"
  chmod 0755 "${myWORK}/overlay/tpot/"*.sh
  chmod 0644 "${myWORK}/overlay/tpot/preseed.cfg" "${myWORK}/overlay/tpot/tpot.issue" \
    "${myWORK}/overlay/tpot/iso.conf" "${myWORK}/overlay/tpot/version"
  PYTHONDONTWRITEBYTECODE=1 python3 "${myDIR}/isotree.py" md5sum --orig "${myWORK}/orig/md5sum.txt" \
    --out "${myWORK}/overlay" || return 1
  while IFS= read -r myFILE; do
    myREL="${myFILE#"${myWORK}/overlay"}"
    myMAP+=(-map "${myFILE}" "${myREL}")
    myPATHS+=("${myREL}")
  done < <(find "${myWORK}/overlay" -type f | sort)
  # the volume ID, the El Torito boot images and the system area (MBR, GPT, the EFI partition) of
  # Debian's netinst, replayed; the new files owned by root and read-only like Debian's
  xorriso -indev "${mySRC}" -outdev "${myWORK}/new.iso" -boot_image any replay "${myMAP[@]}" \
    -chown 0 "${myPATHS[@]}" -- -chgrp 0 "${myPATHS[@]}" -- -chmod a-w "${myPATHS[@]}" -- || return 1
}

fuBOOT_RECORDS () {
  # the boot records of an ISO without the block addresses, which move with the new files: the El
  # Torito images (platform, emulation, size, path, options), the system area and its partitions
  xorriso -indev "$1" -report_el_torito plain -report_system_area plain 2>/dev/null | awk '
    /^El Torito boot img/ { NF--; print; next }
    /^MBR partition +:/ { print $1, $2, $3, $4, $5, $6; next }
    /^(El Torito img (path|opts)|System area summary|(MBR|GPT|APM) partition (path|name|type)|GPT type GUID)/ { $1 = $1; print }'
}

fuCHECK_ISO () {
  # the ISO boots like the netinst: the same boot records, /tpot/preseed.cfg in it
  local myA myB
  myA=$(fuBOOT_RECORDS "$1")
  myB=$(fuBOOT_RECORDS "$2")
  echo "${myB}"
  if [ -z "${myA}" ] || [ "${myA}" != "${myB}" ];
    then
      echo "the boot records differ from the netinst:" >&2
      diff <(echo "${myA}") <(echo "${myB}") >&2
      return 1
  fi
  if ! xorriso -osirrox on -indev "$2" -extract /tpot/preseed.cfg "${myWORK}/check.cfg" >/dev/null 2>&1 \
     || ! cmp -s "${myWORK}/check.cfg" "${myDIR}/preseed.cfg";
    then
      echo "/tpot/preseed.cfg is not in the ISO" >&2
      return 1
  fi
}

fuCHECKSUMS () {
  # fuCHECKSUMS <iso>: <iso>.sha256 and <iso>.sha512 next to it, as sha256sum / shasum -c read them
  local myBITS mySUM
  for myBITS in 256 512; do
    mySUM=$(fuHASH "${myBITS}" "$1") || { echo "cannot read $1" >&2; return 1; }
    echo "${mySUM}  ${1##*/}" > "$1.sha${myBITS}" || return 1
    echo "sha${myBITS} ${mySUM}  ${1##*/}"
  done
}

fuSUM_FILE () {
  # fuSUM_FILE <iso> <256|512>: the checksum of its .sha256 / .sha512, empty without one
  [ -f "$1.sha$2" ] || return 0
  awk -v name="${1##*/}" '($2 == name || $2 == "*" name) { print $1; exit }' "$1.sha$2"
}

fuBUILD_ONE () {
  # fuBUILD_ONE <arch>: the ISO of one architecture here (Linux with the tools, the container of
  # fuDOCKER_ONE): checked netinst, ISO, its boot records, .sha256, .sha512, .buildinfo; its name
  # and the note of the cache into ${myOUT}/.last-<arch> for the run
  local myA="$1" mySRC_ISO myNAME mySRC_SUM mySUMS_URL myNOTE myVER myISO my256 my512
  # work-<pid of the run>-<arch>: fuEXIT of the run finds the one of a container it stopped
  myWORK=$(mktemp -d "${myOUT}/work-${TPOT_ISO_RUN:-$$}-${myA}.XXXXXX") \
    || { fuUI_ERROR "Cannot create a work folder in ${myOUT}."; return 1; }
  fuUI_SPIN "Getting the Debian netinst for ${myA} and checking its signature ..." "${myLOG}" fuNETINST "${myA}" || return $?
  { read -r mySRC_ISO; read -r myNAME; read -r mySRC_SUM; read -r mySUMS_URL; read -r myNOTE; } < "${myWORK}/netinst"
  [ -n "${TPOT_ISO_IN_DOCKER:-}" ] || [ -z "${myNOTE}" ] || fuUI_INFO "${myNOTE}"
  myVER="${myNAME#debian-}"
  myVER="${myVER%%-*}"
  if [ "${myVER%%.*}" != "${myDEBIAN_MAJOR}" ];
    then
      fuUI_ERROR "${myNAME} is Debian ${myVER%%.*}, the T-Pot installer needs Debian ${myDEBIAN_MAJOR}."
      return 1
  fi
  fuUI_SPIN "Writing the T-Pot ISO for ${myA} ..." "${myLOG}" fuBUILD "${myA}" "${mySRC_ISO}" || return $?
  fuUI_SPIN "Checking its boot records ..." "${myLOG}" fuCHECK_ISO "${mySRC_ISO}" "${myWORK}/new.iso" || return $?
  myISO="${myPREFIX}-debian-${myVER}-${myA}.iso"
  rm -f "${myOUT}/${myISO}.sha256" "${myOUT}/${myISO}.sha512" "${myOUT}/${myISO}.buildinfo"
  mv -f "${myWORK}/new.iso" "${myOUT}/${myISO}" || return 1
  fuUI_SPIN "Writing its checksums ..." "${myLOG}" fuCHECKSUMS "${myOUT}/${myISO}" || return $?
  my256=$(fuSUM_FILE "${myOUT}/${myISO}" 256)
  my512=$(fuSUM_FILE "${myOUT}/${myISO}" 512)
  {
    echo "iso: ${myISO}"
    echo "sha256: ${my256}"
    echo "sha512: ${my512}"
    echo "tpot_repository: ${myREPO_URL}"
    echo "tpot_branch: ${myBRANCH}"
    echo "tpot_source_commit: $(fuCOMMIT)"
    echo "tpot_version: $(cat "${myREPO}/version" 2>/dev/null)"
    echo "debian_netinst: ${myNAME}"
    echo "debian_netinst_sha512: ${mySRC_SUM}"
    echo "debian_sha512sums: ${mySUMS_URL}"
    echo "debian_sha512sums_signed_by: ${myDEBIAN_KEY}"
    echo "built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "xorriso: $(xorriso -version 2>/dev/null | head -n 1)"
  } > "${myOUT}/${myISO}.buildinfo"
  printf '%s\n%s\n' "${myISO}" "${myNOTE}" > "${myOUT}/.last-${myA}"
  fuWORK_CLEAN
}

fuTOOLS_MISSING () {
  # the tools of a build here that are missing, empty for none
  local myTOOL myMISSING=""
  for myTOOL in xorriso python3 gpg curl; do
    command -v "${myTOOL}" >/dev/null || myMISSING="${myMISSING} ${myTOOL}"
  done
  command -v sha512sum >/dev/null || command -v shasum >/dev/null || myMISSING="${myMISSING} sha512sum"
  echo "${myMISSING# }"
}

fuHOW () {
  # where the build runs into myHOW (here or docker) and myHOW_TEXT; rc 1 with an error when
  # neither works: -D and outside Linux Docker, on Linux Docker only without the tools
  local myMISSING=""
  myHOW="here" myHOW_TEXT="Build here (Linux with xorriso, python3, gpg and curl)"
  if [ -z "${myDOCKER}" ] && [ "$(uname -s 2>/dev/null)" = "Linux" ];
    then
      myMISSING=$(fuTOOLS_MISSING)
      [ -n "${myMISSING}" ] || return 0
  fi
  myHOW="docker" myHOW_TEXT="Build in Docker (${myDOCKER_IMAGE})"
  [ -z "${myMISSING}" ] || myHOW_TEXT="Build in Docker (${myDOCKER_IMAGE}): ${myMISSING} missing here"
  if ! command -v docker >/dev/null || ! docker info >/dev/null 2>&1;
    then
      if [ -n "${myMISSING}" ];
        then
          fuUI_ERROR "Missing: ${myMISSING}; Docker does not answer either."
          fuUI_HINT "Debian: sudo apt-get install xorriso python3 gpg curl" "or start Docker and run it again" >&2
        else
          fuUI_ERROR "The build needs Docker here (or Linux with xorriso, python3, gpg and curl)."
          fuUI_HINT "Start Docker and run it again." >&2
      fi
      return 1
  fi
}

fuDOCKER_IMAGE () {
  # the Dockerfile on stdin (fuUI_SPIN gives a step /dev/null as its stdin); --pull and the week
  # (REFRESH) in its RUN line: a new Debian base image and new packages at least once a week
  printf '%s\n' "FROM debian:trixie-slim" "ARG REFRESH" \
    "RUN echo \"refresh \${REFRESH}\" && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gpg python3 xorriso && rm -rf /var/lib/apt/lists/*" \
    | docker build --pull --build-arg "REFRESH=$(date +%G-%V)" -t "${myDOCKER_IMAGE}" -
}

fuDOCKER_ONE () {
  # fuDOCKER_ONE <arch>: the build of one architecture in a Debian container, the same script with
  # the source worked out here (fuIN_DOCKER); the log of this run goes on there
  local myA="$1"
  local -a myMOUNTS=(-v "${myREPO}:/src:ro" -v "${myOUT}:/out" -v "${myCACHE}:/cache")
  local -a myARGS=(-a "${myA}" -b "${myBRANCH}" -r "${myREPO_URL}" -o /out)
  if [ -n "${myINPUT}" ];
    then
      myMOUNTS+=(-v "$(dirname -- "${myINPUT}"):/input:ro")
      myARGS+=(-i "/input/$(basename -- "${myINPUT}")")
  fi
  docker run --rm --init --name "tpot-iso-build-$$-${myA}" --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e TPOT_GUM=off -e TPOT_ISO_IN_DOCKER=1 -e "TPOT_ISO_RUN=$$" -e "TPOT_ISO_PREFIX=${myPREFIX}" \
    -e "TPOT_ISO_SOURCE_COMMIT=$(fuCOMMIT)" -e TPOT_ISO_CACHE=/cache "${myMOUNTS[@]}" "${myDOCKER_IMAGE}" \
    bash /src/installer/iso/build.sh "${myARGS[@]}"
}

fuDOCKER_STOP () {
  # the build containers of this run (a cancelled spinner leaves its docker run, not its container)
  local myIDS
  [ -n "${myDOCKER_USED}" ] || return 0
  myIDS=$(docker ps -aq --filter "name=^tpot-iso-build-$$-" 2>/dev/null)
  [ -z "${myIDS}" ] || docker rm -f ${myIDS} >/dev/null 2>&1
  return 0
}

fuIN_DOCKER () {
  # the build of one architecture in the container of fuDOCKER_ONE: plain text, the log of the run
  # goes on (never cut), the name of the ISO in /out/.last-<arch>
  local myMISSING
  myOUT="$(CDPATH="" cd -- "${myOUT}" && pwd)" || return 1
  myLOG="${myOUT}/build.log"
  myPREFIX="${TPOT_ISO_PREFIX:-tpot-${myBRANCH//\//-}}"
  myMISSING=$(fuTOOLS_MISSING)
  [ -z "${myMISSING}" ] || { fuUI_ERROR "Missing in the image ${myDOCKER_IMAGE}: ${myMISSING}."; return 1; }
  trap fuWORK_CLEAN EXIT
  fuBUILD_ONE "${myARCH}"
}

fuEXISTING () {
  # fuEXISTING <path>: the path or the nearest folder above it that exists (a folder not made yet)
  local myP="$1"
  while [ -n "${myP}" ] && [ ! -e "${myP}" ]; do myP=$(dirname -- "${myP}"); done
  echo "${myP:-/}"
}

fuPLIST () {
  # fuPLIST <key path> < plist: one value of a plist of diskutil (plutil of macOS), rc 1 without it
  plutil -extract "$1" raw -o - - 2>/dev/null
}

fuPROTECTED_DARWIN () {
  # fuPROTECTED_DARWIN <disk>: the physical whole disks under a disk of macOS (an APFS container
  # is a synthesized disk on a partition of a physical one: its physical stores)
  local myINFO myWHOLE myN=0 mySTORE myFOUND=""
  myINFO=$(diskutil info -plist "$1" 2>/dev/null) || return 0
  myWHOLE=$(fuPLIST ParentWholeDisk <<< "${myINFO}") || return 0
  myINFO=$(diskutil info -plist "${myWHOLE}" 2>/dev/null) || return 0
  while mySTORE=$(fuPLIST "APFSPhysicalStores.${myN}.APFSPhysicalStore" <<< "${myINFO}"); do
    myFOUND=1
    [ "${2:-0}" -ge 3 ] || fuPROTECTED_DARWIN "${mySTORE}" $(( ${2:-0} + 1 ))
    myN=$((myN + 1))
  done
  [ -n "${myFOUND}" ] || echo "/dev/${myWHOLE}"
}

fuPROTECTED () {
  # fuPROTECTED [path] ...: the whole disks that hold the system, the home folder, the ISOs and
  # the cache (and the paths given), one per line: never a stick to write to
  local myP mySRC
  for myP in / /boot /boot/efi "${HOME}" "${myDIR}/out" "${myCACHE}" ${myOUT:+"${myOUT}"} "$@"; do
    [ -n "${myP}" ] || continue
    myP=$(fuEXISTING "${myP}")
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ];
      then
        mySRC=$(df -P "${myP}" 2>/dev/null | awk 'NR == 2 { print $1 }')
        case "${mySRC}" in /dev/disk*) fuPROTECTED_DARWIN "${mySRC}" ;; esac
      else
        mySRC=$(findmnt -nro SOURCE --target "${myP}" 2>/dev/null | head -n 1)
        # btrfs: /dev/sda2[/@home]
        mySRC="${mySRC%%\[*}"
        [ -n "${mySRC}" ] || mySRC=$(df -P "${myP}" 2>/dev/null | awk 'NR == 2 { print $1 }')
        case "${mySRC}" in
          /dev/*) lsblk -nsrpo NAME,TYPE "${mySRC}" 2>/dev/null | awk '$2 == "disk" { print $1 }' ;;
        esac
    fi
  done | sort -u
}

fuUSB_LINUX () {
  # the USB sticks and SD cards of Linux as fuUSB_LIST prints them: whole disks on USB or SD
  # (TRAN usb, mmc) or with removable media, writable, not empty. lsblk -P quotes each value and
  # writes what is not plain as \xNN; read by a pattern, never run
  local myLINE myKEY myVAL myNAME mySIZE myTYPE myRM myRO myTRAN myVENDOR myMODEL myLABEL myVOLS myBUS
  command -v lsblk >/dev/null || return 0
  while IFS= read -r myLINE; do
    myNAME="" mySIZE="" myTYPE="" myRM="" myRO="" myTRAN="" myVENDOR="" myMODEL=""
    while [[ "${myLINE}" =~ ^[[:space:]]*([A-Z-]+)=\"([^\"]*)\"(.*)$ ]]; do
      myKEY="${BASH_REMATCH[1]}" myVAL="${BASH_REMATCH[2]}" myLINE="${BASH_REMATCH[3]}"
      printf -v myVAL '%b' "${myVAL}"
      case "${myKEY}" in
        NAME) myNAME="${myVAL}" ;; SIZE) mySIZE="${myVAL}" ;; TYPE) myTYPE="${myVAL}" ;;
        RM) myRM="${myVAL}" ;; RO) myRO="${myVAL}" ;; TRAN) myTRAN="${myVAL}" ;;
        VENDOR) myVENDOR="${myVAL}" ;; MODEL) myMODEL="${myVAL}" ;;
      esac
    done
    [ "${myTYPE}" = "disk" ] && [ "${myRO}" != "1" ] && [[ "${mySIZE}" =~ ^[1-9][0-9]*$ ]] || continue
    case "${myTRAN}" in usb) myBUS="USB" ;; mmc) myBUS="SD card" ;; *) [ "${myRM}" = "1" ] || continue; myBUS="removable" ;; esac
    myLABEL=$(echo "${myVENDOR} ${myMODEL}" | awk '{ $1 = $1; print }')
    myVOLS=$(lsblk -nrpo LABEL "${myNAME}" 2>/dev/null | while IFS= read -r myVAL; do
               [ -z "${myVAL}" ] || printf '%b\n' "${myVAL}"; done | paste -sd ',' - | sed 's/,/, /g')
    myLABEL="${myLABEL:-Drive} · $(fuHUMAN "${mySIZE}") · ${myBUS} · ${myNAME}"
    [ -z "${myVOLS}" ] || myLABEL="${myLABEL} · ${myVOLS}"
    echo "${myNAME}|${mySIZE}|${myLABEL}"
  done < <(lsblk -dpnbP -o NAME,SIZE,TYPE,RM,RO,TRAN,VENDOR,MODEL 2>/dev/null)
}

fuUSB_DARWIN () {
  # the USB sticks and SD cards of macOS as fuUSB_LIST prints them: external physical whole disks
  # (no disk images), not internal, writable
  local myLIST myINFO myDISK myN=0 myV mySIZE myNAME myBUS myVOLS myLABEL myVOL
  myLIST=$(diskutil list -plist external physical 2>/dev/null) || return 0
  while myDISK=$(fuPLIST "WholeDisks.${myN}" <<< "${myLIST}"); do
    myN=$((myN + 1))
    myINFO=$(diskutil info -plist "${myDISK}" 2>/dev/null) || continue
    [ "$(fuPLIST Internal <<< "${myINFO}")" = "false" ] || continue
    [ "$(fuPLIST VirtualOrPhysical <<< "${myINFO}")" != "Virtual" ] || continue
    [ "$(fuPLIST WritableMedia <<< "${myINFO}")" != "false" ] || continue
    mySIZE=$(fuPLIST Size <<< "${myINFO}") || mySIZE=$(fuPLIST TotalSize <<< "${myINFO}") || continue
    [[ "${mySIZE}" =~ ^[1-9][0-9]*$ ]] || continue
    myNAME=$(fuPLIST MediaName <<< "${myINFO}" | awk '{ $1 = $1; print }')
    myBUS=$(fuPLIST BusProtocol <<< "${myINFO}")
    myVOLS="" myV=0
    while myVOL=$(diskutil list -plist "${myDISK}" 2>/dev/null | fuPLIST "VolumesFromDisks.${myV}"); do
      myVOLS="${myVOLS:+${myVOLS}, }${myVOL}"
      myV=$((myV + 1))
    done
    myLABEL="${myNAME:-Drive} · $(fuHUMAN "${mySIZE}") · ${myBUS:-external} · /dev/${myDISK}"
    [ -z "${myVOLS}" ] || myLABEL="${myLABEL} · ${myVOLS}"
    echo "/dev/${myDISK}|${mySIZE}|${myLABEL}"
  done
}

fuUSB_LIST () {
  # fuUSB_LIST [path] ...: the sticks it may write to, one per line: device|bytes|label, i.e.
  # /dev/sdb|32015679488|SanDisk Cruzer Blade · 32.0 GB · USB · /dev/sdb · STICK; never one of
  # fuPROTECTED (the paths given are protected too, i.e. the folder of the ISO)
  local myPROTECTED myLINE
  myPROTECTED=$(fuPROTECTED "$@")
  while IFS= read -r myLINE; do
    [ -n "${myLINE}" ] || continue
    grep -qxF -- "${myLINE%%|*}" <<< "${myPROTECTED}" || echo "${myLINE}"
  done < <(if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then fuUSB_DARWIN; else fuUSB_LINUX; fi)
}

fuDEVICE () {
  # fuDEVICE <device>: its whole disk by one name (macOS: disk4, /dev/rdisk4 and /dev/disk4 are
  # /dev/disk4; Linux: sdb is /dev/sdb, a link of /dev/disk/by-id its target); rc 1 for none
  local myD="$1"
  if [ "$(uname -s 2>/dev/null)" = "Darwin" ];
    then
      myD="${myD#/dev/}"
      myD="${myD#r}"
      [[ "${myD}" =~ ^disk[0-9]+$ ]] || return 1
      echo "/dev/${myD}"
    else
      case "${myD}" in /*) ;; *) myD="/dev/${myD}" ;; esac
      # the list of fuUSB_LIST decides, a name that is no disk is not in it
      readlink -f -- "${myD}" 2>/dev/null || return 1
  fi
}

fuUSB_FIND () {
  # fuUSB_FIND <device> [path] ...: its line of fuUSB_LIST, rc 1 when it is none of them
  local myD="$1" myLINE
  shift
  myD=$(fuDEVICE "${myD}") || return 1
  while IFS= read -r myLINE; do
    [ "${myLINE%%|*}" != "${myD}" ] || { echo "${myLINE}"; return 0; }
  done < <(fuUSB_LIST "$@")
  return 1
}

fuLIST () {
  # -l: the sticks, the disks it never writes to
  local myLINE myN=0 myPROTECTED
  fuUI_BANNER "ISO Builder (preview)" "USB sticks and SD cards it may write to"
  while IFS= read -r myLINE; do
    [ -n "${myLINE}" ] || continue
    fuUI_INFO "${myLINE%%|*}: ${myLINE##*|}"
    myN=$((myN + 1))
  done < <(fuUSB_LIST)
  [ "${myN}" -gt 0 ] || fuUI_WARN "None found: plug in a USB stick (or an SD card) and look again."
  myPROTECTED=$(fuPROTECTED | paste -sd ',' - | sed 's/,/, /g')
  [ -z "${myPROTECTED}" ] || fuUI_HINT "Never written to: ${myPROTECTED} (the system, the home folder, the ISOs)."
  return 0
}

fuWLOG () {
  # the log of writing a stick (the log of a build is in its output folder)
  myWROTE_LOG="${myDIR}/out/write.log"
  mkdir -p "${myDIR}/out" 2>/dev/null && : > "${myWROTE_LOG}" 2>/dev/null || myWROTE_LOG="/dev/null"
}

fuISO_SUMS () {
  # fuISO_SUMS <iso>: rc 0 when it matches its .sha256 and .sha512 (those that are there)
  local myBITS myWANT myHAVE
  for myBITS in 256 512; do
    myWANT=$(fuSUM_FILE "$1" "${myBITS}")
    [ -n "${myWANT}" ] || continue
    myHAVE=$(fuHASH "${myBITS}" "$1") || { echo "cannot read $1" >&2; return 1; }
    [ "${myHAVE}" = "${myWANT}" ] || { echo "$1: sha${myBITS} ${myHAVE}, its .sha${myBITS} says ${myWANT}" >&2; return 1; }
    echo "$1: sha${myBITS} matches its .sha${myBITS}"
  done
}

fuISO_CHECK () {
  # fuISO_CHECK <iso>: an ISO is written only as it was built (its checksum files); one without
  # them as it is, with a warning
  if [ -z "$(fuSUM_FILE "$1" 256)$(fuSUM_FILE "$1" 512)" ];
    then
      fuUI_WARN "${1##*/} has no .sha256 or .sha512 next to it: it is written as it is."
      return 0
  fi
  fuUI_SPIN "Checking ${1##*/} against its checksums ..." "${myWROTE_LOG}" fuISO_SUMS "$1"
}

fuDD_FLAGS () {
  # fuDD_FLAGS <write|read>: GNU dd writes and reads past the cache (and syncs), the progress where
  # dd shows one (GNU, macOS 13 and newer)
  if dd --version 2>/dev/null | grep -q coreutils;
    then
      if [ "$1" = "write" ]; then echo "conv=fsync oflag=direct status=progress"; else echo "iflag=direct"; fi
  elif [ "$1" = "write" ] && dd if=/dev/zero of=/dev/null count=1 status=progress >/dev/null 2>&1;
    then echo "status=progress"
  fi
}

fuUNMOUNT () {
  # fuUNMOUNT <device>: none of its volumes mounted (a desktop mounts a stick when it comes);
  # mySUDO of fuWRITE where a user cannot unmount them
  local myPART
  if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then diskutil unmountDisk "$1" >/dev/null; return; fi
  while IFS= read -r myPART; do
    [ -n "${myPART}" ] || continue
    umount "${myPART}" 2>/dev/null \
      || { command -v udisksctl >/dev/null && udisksctl unmount --no-user-interaction -b "${myPART}" >/dev/null 2>&1; } \
      || "${mySUDO[@]}" umount "${myPART}" || return 1
  done < <(lsblk -nrpo NAME,MOUNTPOINT "$1" 2>/dev/null | awk '$2 != "" { print $1 }')
}

fuSTICK_CHECK () {
  # fuSTICK_CHECK <device> <iso> <bytes> [sha256]: the first bytes of the stick (as many as the ISO
  # has) read back, their sha256 the one of the ISO; sudo -n (fuWRITE refreshed it), no prompt. rc 1
  # for another sha256, 2 when the stick cannot be read
  local myWANT="${4:-}" myHAVE myBS=4194304
  local -a myRUN=() myFLAGS=()
  [ "${#mySUDO[@]}" -eq 0 ] || myRUN=(sudo -n)
  if [ "${#myRUN[@]}" -gt 0 ] && ! sudo -n true 2>/dev/null; then echo "sudo -n: no password cached to read $1" >&2; return 2; fi
  read -r -a myFLAGS <<< "$(fuDD_FLAGS read)"
  [ -n "${myWANT}" ] || myWANT=$(fuHASH 256 "$2") || { echo "cannot read $2" >&2; return 2; }
  # the pipe below has the rc of the hash (dd gets SIGPIPE once head has its bytes): can dd read at all?
  "${myRUN[@]}" dd if="$1" of=/dev/null bs=4096 count=1 "${myFLAGS[@]}" 2>/dev/null || { echo "cannot read $1" >&2; return 2; }
  myHAVE=$("${myRUN[@]}" dd if="$1" bs="${myBS}" count=$(( ($3 + myBS - 1) / myBS )) "${myFLAGS[@]}" 2>/dev/null \
           | head -c "$3" | fuHASH 256 -) || { echo "cannot read $1" >&2; return 2; }
  if [ "${myHAVE}" != "${myWANT}" ];
    then
      echo "$1: sha256 ${myHAVE}, the ISO has ${myWANT}" >&2
      return 1
  fi
  echo "$1: sha256 ${myHAVE} of the first $3 bytes, the one of $2"
}

fuEJECT () {
  # fuEJECT <device>: rc 0 when it is gone (safe to pull out)
  if [ "$(uname -s 2>/dev/null)" = "Darwin" ];
    then diskutil eject "$1" >/dev/null 2>&1
    else command -v udisksctl >/dev/null && udisksctl power-off --no-user-interaction -b "$1" >/dev/null 2>&1
  fi
}

fuCONFIRM_WRITE () {
  # fuCONFIRM_WRITE <iso> <line of fuUSB_LIST>: what is erased, the same without the menu; 0 for
  # erase and write
  local myLINE="$2" mySIZE
  local -a myITEMS=()
  mySIZE="${myLINE#*|}"
  mySIZE="${mySIZE%%|*}"
  myITEMS=("info:ISO: $1 ($(fuHUMAN "$(fuSIZE "$1")"))" "info:Stick: ${myLINE##*|}"
           "warn:Everything on it is erased: all its partitions and files")
  [ "${mySIZE}" -le "${myBIG_STICK}" ] \
    || myITEMS+=("warn:It holds $(fuHUMAN "${mySIZE}"), more than a USB stick usually does: make sure it is no backup disk")
  myITEMS+=("next:The same without the menu: $(fuCOMMAND_LINE write "$1" "${myLINE%%|*}")")
  fuUI_SUMMARY "Ready to write" "${myITEMS[@]}"
  fuYES --default no "Erase ${myLINE##*|} and write the ISO?" "Erase and write" "Back"
}

fuWRITE () {
  # fuWRITE <iso> <device>: the ISO onto the stick (asked before, fuCONFIRM_WRITE or -y): the
  # stick checked again, unmounted, written (dd, sudo where the user may not), read back and
  # checked, ejected
  local myISO="$1" myLINE myDEV mySIZE myLABEL myISO_SIZE myTARGET myWANT myBOOT="BIOS or UEFI" myRC=0
  local -a mySUDO=() myFLAGS=() myITEMS=()
  [ -n "${myWROTE_LOG}" ] || fuWLOG
  if ! myLINE=$(fuUSB_FIND "$2" "$(dirname -- "${myISO}")");
    then
      fuUI_ERROR "$2 is no USB stick or SD card it may write to (any more)."
      fuUI_HINT "installer/iso/build.sh -l lists them." >&2
      return 1
  fi
  myDEV="${myLINE%%|*}" mySIZE="${myLINE#*|}" myLABEL="${myLINE##*|}"
  mySIZE="${mySIZE%%|*}"
  myISO_SIZE=$(fuSIZE "${myISO}") || { fuUI_ERROR "Cannot read ${myISO}."; return 1; }
  if [ "${myISO_SIZE}" -gt "${mySIZE}" ];
    then
      fuUI_ERROR "${myISO##*/} ($(fuHUMAN "${myISO_SIZE}")) does not fit on ${myLABEL}."
      return 1
  fi
  myWANT=$(fuSUM_FILE "${myISO}" 256)
  myTARGET="${myDEV}"
  # macOS: the raw device, many times faster
  [ "$(uname -s 2>/dev/null)" != "Darwin" ] || myTARGET="/dev/r${myDEV#/dev/}"
  if [ "$(id -u)" -ne 0 ] && [ ! -w "${myTARGET}" ];
    then
      command -v sudo >/dev/null || { fuUI_ERROR "Writing to ${myDEV} needs root, sudo is missing."; return 1; }
      mySUDO=(sudo)
      fuUI_INFO "Writing to ${myDEV} needs root: sudo may ask for your password."
      sudo -v || { fuUI_ERROR "sudo failed, nothing was written."; return 1; }
  fi
  if ! fuUNMOUNT "${myDEV}";
    then
      fuUI_ERROR "Cannot unmount the volumes of ${myDEV}, nothing was written."
      fuUI_HINT "Close what uses them (a file manager, a terminal in them) and try again." >&2
      return 1
  fi
  read -r -a myFLAGS <<< "$(fuDD_FLAGS write)"
  fuUI_INFO "Writing ${myISO##*/} ($(fuHUMAN "${myISO_SIZE}")) to ${myLABEL} ..."
  "${mySUDO[@]}" dd if="${myISO}" of="${myTARGET}" bs=4194304 "${myFLAGS[@]}" || myRC=$?
  sync
  if [ "${myRC}" -ne 0 ];
    then
      fuUI_ERROR "Writing to ${myDEV} failed (dd: ${myRC}): the stick is not usable now, write it again."
      return 1
  fi
  fuUI_OK "Written"
  [ "${#mySUDO[@]}" -eq 0 ] || sudo -v || return 1
  fuUI_SPIN "Reading the stick back and checking it ..." "${myWROTE_LOG}" fuSTICK_CHECK "${myTARGET}" "${myISO}" \
    "${myISO_SIZE}" "${myWANT}" || myRC=$?
  [ "${myRC}" -ne 130 ] || return 130
  [[ "${myISO}" != *-arm64.iso ]] || myBOOT="UEFI"
  myITEMS=("ok:${myISO##*/} on ${myLABEL}")
  case "${myRC}" in
    0) myITEMS+=("ok:Read back: the same sha256 as the ISO") ;;
    2) myITEMS+=("warn:Not read back (the end of $(fuSHORT "${myWROTE_LOG}") says why): it is written, but not checked") ;;
    *) myITEMS+=("fail:Read back: not the ISO, write it again or take another stick") ;;
  esac
  # a desktop mounts the new partitions of the ISO at once
  fuUNMOUNT "${myDEV}" >/dev/null 2>&1
  if fuEJECT "${myDEV}";
    then myITEMS+=("ok:Ejected, pull it out")
    else myITEMS+=("next:Remove the stick")
  fi
  [ "$(uname -s 2>/dev/null)" != "Darwin" ] \
    || myITEMS+=("info:macOS may say it cannot read the stick: Ignore or Eject, macOS does not know the file systems of the ISO")
  myITEMS+=("next:Boot from it (${myBOOT}), choose Install T-Pot; the T-Pot installer starts at the first login")
  fuUI_SUMMARY "USB stick" "${myITEMS[@]}"
}

fuASK () {
  # fuASK <variable> <fuUI_CHOOSE | fuUI_INPUT> <argument> ...: the answer into the variable; a
  # cancelled question (gum, end of input) ends the builder
  local myASK_VAR="$1" myASK_OUT
  shift
  myASK_OUT=$("$@") || exit 130
  printf -v "${myASK_VAR}" '%s' "${myASK_OUT}"
}

fuYES () {
  # fuYES [--default yes|no] <question> [yes] [no]: fuUI_CONFIRM, 0 for yes, 1 for no; cancelled
  # (gum ctrl+c) it ends the builder
  local myRC=0
  fuUI_CONFIRM "$@" || myRC=$?
  [ "${myRC}" -le 1 ] || exit 130
  return "${myRC}"
}

fuQ () {
  # a path for a command line to copy
  printf '%q' "$1"
}

fuDEF_ARCH () {
  # the architecture without -a: both for a release, else the one of this machine
  if [ -n "${myRELEASE}" ]; then echo "both"; else fuHOST_ARCH; fi
}

fuCOMMAND_LINE () {
  # the same run without the menu: fuCOMMAND_LINE [write <iso> <device>]. -a always (with no option
  # at all the builder asks), the rest where it is no default
  local -a myC=("${mySELF}")
  if [ "${1:-}" = "write" ];
    then myC+=(-w "$2" -d "$3")
    else
      myC+=(-a "${myARCH}")
      [ "${myBRANCH}" = "${myDEF_BRANCH}" ] || myC+=(-b "${myBRANCH}")
      [ "${myREPO_URL}" = "${myDEF_REPO_URL}" ] || myC+=(-r "${myREPO_URL}")
      [ -z "${myINPUT}" ] || myC+=(-i "${myINPUT}")
      [ -z "${myOUT_GIVEN}" ] || myC+=(-o "${myOUT}")
      [ -z "${myDOCKER}" ] || myC+=(-D)
      [ -z "${myRELEASE}" ] || myC+=(-R)
      [ -z "${mySIGN_FPR}" ] || myC+=(-s "${mySIGN_FPR}")
      [ -z "${myUPLOAD_TAG}" ] || myC+=(-U "${myUPLOAD_TAG}")
      [ -z "${myDEVICE}" ] || myC+=(-d "${myDEVICE}")
  fi
  printf '%q ' "${myC[@]}" | sed 's/ $//'
}

fuDESCRIBE () {
  # one line of what a build makes
  local myWHAT="The ISO for ${myARCH}"
  [ "${myARCH}" != "both" ] || myWHAT="The ISOs for amd64 and arm64"
  [ -z "${myRELEASE}" ] || myWHAT="${myWHAT} of T-Pot ${myVERSION:-$(fuUI_VERSION "${myREPO}")}"
  echo "${myWHAT}, installing T-Pot from ${myREPO_URL} at ${myBRANCH}"
}

fuEXIT () {
  # the end of every run (EXIT): the work folders (also those of a container stopped), the build
  # containers, what the builds left for the run; more than once does no harm
  local myA myW
  fuWORK_CLEAN
  fuDOCKER_STOP
  if [ -n "${myOUT}" ] && [ -d "${myOUT}" ]; then
    for myW in "${myOUT}"/work-"$$"-*; do
      [ -d "${myW}" ] || continue
      chmod -R u+w "${myW}" 2>/dev/null
      rm -rf "${myW}"
    done
    for myA in amd64 arm64; do rm -f "${myOUT}/.last-${myA}"; done
  fi
  [ -z "${myPEEK_FILE}" ] || rm -f "${myPEEK_FILE}"
  myPEEK_FILE=""
  return 0
}

fuCANCEL () {
  # INT / TERM: what the run started ends (a sudo dd gets the INT of the terminal itself), then
  # fuEXIT, exit 130; a second signal does not cut that short
  local myP
  trap '' INT TERM
  echo
  fuUI_WARN "Cancelled."
  for myP in $(fuUI_TREE "$$" | tail -n +2); do kill -TERM "${myP}" 2>/dev/null; done
  fuEXIT
  exit 130
}

fuRELEASE_CHECK () {
  # -R: the version of the checkout, the checkout clean, its HEAD the branch of the repository. A
  # branch, no tag or commit: the ISO installs it with git clone, update.sh of that T-Pot refuses a
  # detached HEAD. rc 1 with the error
  local myHEAD myLS myRC=0 myAT
  myVERSION=$(fuUI_VERSION "${myREPO}")
  [ -n "${myVERSION}" ] || { fuUI_ERROR "${myREPO}/version holds no version."; return 1; }
  myHEAD=$(fuGIT rev-parse HEAD)
  if [ -z "${myHEAD}" ];
    then
      fuUI_ERROR "The ISOs of a release are built from a git checkout (git is missing or ${myREPO} is none)."
      return 1
  fi
  if [ -n "$(fuGIT status --porcelain)" ];
    then
      fuUI_ERROR "The checkout has changes: the ISOs of a release are built from a clean one."
      fuUI_HINT "git status shows them: commit and push them, or git stash." >&2
      return 1
  fi
  if [[ "${myBRANCH}" =~ ^[0-9a-f]{40}$ ]];
    then
      fuUI_ERROR "${myBRANCH} is a commit: the ISOs of a release install a branch (update.sh updates a branch only)."
      return 1
  fi
  myLS=$(GIT_TERMINAL_PROMPT=0 fuTIMEOUT 30 git ls-remote --exit-code "${myREPO_URL}" "refs/heads/${myBRANCH}" 2>/dev/null) \
    || myRC=$?
  if [ "${myRC}" -eq 2 ];
    then
      if GIT_TERMINAL_PROMPT=0 fuTIMEOUT 30 git ls-remote --exit-code "${myREPO_URL}" "refs/tags/${myBRANCH}" >/dev/null 2>&1;
        then
          fuUI_ERROR "${myBRANCH} is a tag: the ISOs of a release install a branch (update.sh of T-Pot refuses a detached HEAD)."
          fuUI_HINT "Build them from the branch of the release, i.e. -b master; -U ${myBRANCH} uploads them to the release of the tag." >&2
        else
          fuUI_ERROR "${myREPO_URL} has no branch ${myBRANCH}."
          fuUI_HINT "git push it first." >&2
      fi
      return 1
  elif [ "${myRC}" -ne 0 ];
    then
      fuUI_ERROR "${myREPO_URL} cannot be reached: a release must know that it has ${myBRANCH}."
      return 1
  fi
  myAT=$(awk 'NR == 1 { print $1 }' <<< "${myLS}")
  if [ "${myAT}" != "${myHEAD}" ];
    then
      fuUI_ERROR "${myREPO_URL} has ${myBRANCH} at ${myAT:0:7}, this checkout is at ${myHEAD:0:7}."
      fuUI_HINT "The ISOs carry install.sh of this checkout and install ${myBRANCH} of the repository: git push (or git pull) first." >&2
      return 1
  fi
  [ "${myBRANCH}" = "master" ] || fuUI_WARN "The ISOs install ${myBRANCH}, a release usually master."
  return 0
}

fuGPG_KEYS () {
  # fuGPG_KEYS [key]: the secret keys of gpg that may sign (not expired, revoked, invalid or
  # disabled), fingerprint|user ID per line; with a key only those it names
  command -v gpg >/dev/null || return 0
  gpg --batch --list-secret-keys --with-colons ${1:+"$1"} 2>/dev/null | awk -F: '
    $1 == "sec" { want = ($2 !~ /^[erid]/ && $12 ~ /S/); fpr = ""; next }
    $1 == "fpr" && want && fpr == "" { fpr = $10; next }
    $1 == "uid" && want && fpr != "" { print fpr "|" $10; want = 0 }'
}

fuSIGN_KEY () {
  # -s: the fingerprint of its key into mySIGN_FPR (the menu sets it itself); rc 1 with the error
  local myKEYS
  [ -n "${mySIGN_KEY}" ] || return 0
  command -v gpg >/dev/null || { fuUI_ERROR "gpg is missing for -s."; return 1; }
  myKEYS=$(fuGPG_KEYS "${mySIGN_KEY}")
  if [ -z "${myKEYS}" ];
    then
      fuUI_ERROR "gpg has no secret key that signs for ${mySIGN_KEY}."
      fuUI_HINT "gpg --list-secret-keys shows them." >&2
      return 1
  fi
  if [ "$(grep -c . <<< "${myKEYS}")" -gt 1 ];
    then
      fuUI_ERROR "More than one key fits ${mySIGN_KEY}: -s takes its fingerprint."
      return 1
  fi
  mySIGN_FPR="${myKEYS%%|*}"
}

fuSIGN () {
  # fuSIGN <file>: <file>.asc, signed by mySIGN_FPR and checked; in the foreground, the pinentry of
  # gpg may ask for the passphrase
  local myTTY
  myTTY=$(tty 2>/dev/null) || myTTY=""
  rm -f "$1.asc"
  if ! GPG_TTY="${myTTY}" gpg --yes --local-user "${mySIGN_FPR}" --armor --detach-sign --output "$1.asc" "$1";
    then
      fuUI_ERROR "gpg did not sign ${1##*/}."
      rm -f "$1.asc"
      return 1
  fi
  if ! gpg --batch --status-fd 1 --verify "$1.asc" "$1" 2>/dev/null \
       | awk -v fpr="${mySIGN_FPR}" '$2 == "VALIDSIG" && ($3 == fpr || $NF == fpr) { ok = 1 } END { exit !ok }';
    then
      fuUI_ERROR "The signature of ${1##*/} is not a valid one of ${mySIGN_FPR}."
      rm -f "$1.asc"
      return 1
  fi
  fuUI_OK "Signed ${1##*/}"
}

fuGH_REPO_OF () {
  # fuGH_REPO_OF <url>: owner/repository of a GitHub URL, rc 1 for another
  [[ "$1" =~ ^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$ ]] || return 1
  echo "${BASH_REMATCH[1]}"
}

fuUPLOAD_CHECK () {
  # -U: gh sees the release of the tag in the repository of -r; rc 1 with the error
  [ -n "${myUPLOAD_TAG}" ] || return 0
  if ! myGH_REPO=$(fuGH_REPO_OF "${myREPO_URL}");
    then
      fuUI_ERROR "-U uploads to a release on GitHub: ${myREPO_URL} is no repository there."
      return 1
  fi
  if ! command -v gh >/dev/null;
    then
      fuUI_ERROR "gh (the GitHub CLI) is missing for -U."
      fuUI_HINT "https://cli.github.com, then gh auth login" >&2
      return 1
  fi
  if ! gh release view "${myUPLOAD_TAG}" --repo "${myGH_REPO}" >/dev/null 2>&1;
    then
      fuUI_ERROR "gh cannot see the release ${myUPLOAD_TAG} of ${myGH_REPO}."
      fuUI_HINT "gh auth status; gh release create ${myUPLOAD_TAG} --repo ${myGH_REPO} --draft makes one." >&2
      return 1
  fi
}

fuRELEASE_FILES () {
  # the files of the release of this run, one per line (no glob: never one of an earlier run)
  local myISO myF
  for myISO in "${myBUILT[@]}"; do
    for myF in "${myISO}" "${myISO}.sha256" "${myISO}.sha512" "${myISO}.buildinfo"; do echo "${myOUT}/${myF}"; done
  done
  for myF in SHA256SUMS SHA512SUMS SHA256SUMS.asc SHA512SUMS.asc; do
    [ ! -f "${myOUT}/${myF}" ] || echo "${myOUT}/${myF}"
  done
}

fuWRITE_SUMS () {
  # SHA256SUMS and SHA512SUMS of the ISOs of this run, each ISO hashed once more and compared with
  # its .sha256 / .sha512; both or none (a failed one leaves no SUMS file)
  local myBITS myISO mySUM
  rm -f "${myOUT}/SHA256SUMS" "${myOUT}/SHA512SUMS"
  for myBITS in 256 512; do
    : > "${myOUT}/SHA${myBITS}SUMS.tmp" || { rm -f "${myOUT}"/SHA*SUMS.tmp; return 1; }
    for myISO in "${myBUILT[@]}"; do
      mySUM=$(fuHASH "${myBITS}" "${myOUT}/${myISO}") \
        || { echo "cannot read ${myISO}" >&2; rm -f "${myOUT}"/SHA*SUMS.tmp; return 1; }
      if [ "${mySUM}" != "$(fuSUM_FILE "${myOUT}/${myISO}" "${myBITS}")" ];
        then
          echo "${myISO}: its sha${myBITS} is not the one of its .sha${myBITS}" >&2
          rm -f "${myOUT}"/SHA*SUMS.tmp
          return 1
      fi
      echo "${mySUM}  ${myISO}" >> "${myOUT}/SHA${myBITS}SUMS.tmp"
    done
  done
  for myBITS in 256 512; do
    mv -f "${myOUT}/SHA${myBITS}SUMS.tmp" "${myOUT}/SHA${myBITS}SUMS" || return 1
    echo "SHA${myBITS}SUMS: ${myBUILT[*]}"
  done
}

fuUPLOAD () {
  # the files of the release onto the GitHub release of the tag (--clobber: those of an earlier
  # upload with the same names are replaced)
  local -a myFILES=()
  local myF
  while IFS= read -r myF; do myFILES+=("${myF}"); done < <(fuRELEASE_FILES)
  gh release upload "${myUPLOAD_TAG}" "${myFILES[@]}" --clobber --repo "${myGH_REPO}"
}

fuRELEASE_OLD () {
  # the files of an earlier release build in the output folder, names on one line
  local myF myOLD=""
  for myF in "${myOUT}"/tpot-*.iso "${myOUT}"/SHA256SUMS "${myOUT}"/SHA512SUMS "${myOUT}"/SHA256SUMS.asc \
             "${myOUT}"/SHA512SUMS.asc; do
    [ ! -e "${myF}" ] || myOLD="${myOLD:+${myOLD}, }${myF##*/}"
  done
  echo "${myOLD}"
}

fuOUT_DEFAULT () {
  # where the ISOs go without -o
  [ -n "${myOUT_GIVEN}" ] && return 0
  myOUT="${myDIR}/out"
  [ -z "${myRELEASE}" ] || myOUT="${myDIR}/out/release-${myVERSION}"
}

fuRUN () {
  # the builds of a run, the same from the menu and without it (fuCLI_BUILD shows the banner and
  # asks before): rc 0 done, 1 failed, 130 cancelled
  local myA myRC=0 myISO myNOTE myOLD myF myBOOT myCHECK="sha256sum -c" myGH
  local -a myITEMS=() myFILES=() myOK=()
  myBUILT=()
  fuHOW || return 1
  # the menu checked them right before its summary
  if [ -n "${myRELEASE}" ] && [ -z "${myMENU}" ]; then
    fuRELEASE_CHECK && fuSIGN_KEY && fuUPLOAD_CHECK || return 1
  fi
  fuOUT_DEFAULT
  if [ -n "${myRELEASE}" ]; then
    myOLD=$(fuRELEASE_OLD)
    if [ -n "${myOLD}" ] && [ -z "${myYES}" ] && [ -z "${myMENU}" ]; then
      if ! [ -t 0 ] || ! [ -t 1 ];
        then
          fuUI_ERROR "${myOUT} has the files of an earlier build: ${myOLD}."
          fuUI_HINT "-y replaces them, -o takes another folder." >&2
          return 1
      fi
      fuYES --default no "${myOUT} has the files of an earlier build (${myOLD}): replace them?" "Replace" "Cancel" \
        || { fuUI_INFO "Nothing was built."; return 0; }
    fi
    if [ -n "${myOLD}" ]; then
      rm -f "${myOUT}"/tpot-*.iso "${myOUT}"/tpot-*.iso.sha256 "${myOUT}"/tpot-*.iso.sha512 "${myOUT}"/tpot-*.iso.buildinfo \
        "${myOUT}"/SHA256SUMS "${myOUT}"/SHA512SUMS "${myOUT}"/SHA256SUMS.asc "${myOUT}"/SHA512SUMS.asc
    fi
  fi
  if ! mkdir -p "${myOUT}" "${myCACHE}" 2>/dev/null;
    then fuUI_ERROR "Cannot create ${myOUT} or ${myCACHE}."; return 1
  fi
  myOUT="$(CDPATH="" cd -- "${myOUT}" && pwd)"
  myCACHE="$(CDPATH="" cd -- "${myCACHE}" && pwd)"
  myLOG="${myOUT}/build.log"
  : > "${myLOG}" 2>/dev/null || { fuUI_ERROR "Cannot write ${myLOG}."; return 1; }
  if [ -n "${myRELEASE}" ];
    then myPREFIX="tpot-${myVERSION}"
  elif [[ "${myBRANCH}" =~ ^[0-9a-f]{40}$ ]];
    then myPREFIX="tpot-${myBRANCH:0:12}"
    else myPREFIX="tpot-${myBRANCH//\//-}"
  fi
  [ -z "${myMENU}" ] || fuUI_INFO "Building: $(fuDESCRIBE)"
  fuUI_INFO "${myHOW_TEXT}; the log: ${myLOG}"
  [ -n "${myRELEASE}" ] || fuCHECK_REMOTE
  for myA in "${myARCHS[@]}"; do
    rm -f "${myOUT}/.last-${myA}"
    if [ "${myHOW}" = "here" ];
      then
        fuBUILD_ONE "${myA}" || myRC=$?
        fuWORK_CLEAN
      else
        if [ -z "${myDOCKER_USED}" ]; then
          myDOCKER_USED=1
          fuUI_SPIN "Preparing the Docker image ${myDOCKER_IMAGE} ..." "${myLOG}" fuDOCKER_IMAGE || myRC=$?
        fi
        [ "${myRC}" -ne 0 ] || fuUI_SPIN "Building the ISO for ${myA} in Docker ..." "${myLOG}" fuDOCKER_ONE "${myA}" || myRC=$?
        [ "${myRC}" -ne 130 ] || fuDOCKER_STOP
    fi
    [ "${myRC}" -ne 130 ] || return 130
    if [ "${myRC}" -ne 0 ] || [ ! -s "${myOUT}/.last-${myA}" ]; then
      [ "${myRC}" -ne 0 ] || myRC=1
      myITEMS+=("fail:The ISO for ${myA}")
      break
    fi
    { read -r myISO; read -r myNOTE; } < "${myOUT}/.last-${myA}"
    rm -f "${myOUT}/.last-${myA}"
    [ "${myHOW}" = "here" ] || [ -z "${myNOTE}" ] || fuUI_INFO "${myNOTE}"
    myBUILT+=("${myISO}")
  done
  if [ "${myRC}" -eq 0 ] && [ -n "${myRELEASE}" ]; then
    rm -f "${myOUT}/SHA256SUMS.asc" "${myOUT}/SHA512SUMS.asc"
    fuUI_SPIN "Writing SHA256SUMS and SHA512SUMS ..." "${myLOG}" fuWRITE_SUMS || myRC=$?
    [ "${myRC}" -ne 130 ] || return 130
    if [ "${myRC}" -eq 0 ] && [ -n "${mySIGN_FPR}" ]; then
      fuUI_INFO "Signing SHA256SUMS and SHA512SUMS with ${mySIGN_FPR} (gpg may ask for the passphrase) ..."
      fuSIGN "${myOUT}/SHA256SUMS" && fuSIGN "${myOUT}/SHA512SUMS" || myRC=1
    fi
    if [ "${myRC}" -eq 0 ] && [ -n "${myUPLOAD_TAG}" ]; then
      fuUI_SPIN "Uploading to the release ${myUPLOAD_TAG} of ${myGH_REPO} ..." "${myLOG}" fuUPLOAD || myRC=$?
      [ "${myRC}" -ne 130 ] || return 130
      [ "${myRC}" -eq 0 ] || myITEMS+=("fail:Upload to the release ${myUPLOAD_TAG} of ${myGH_REPO}")
    fi
  fi
  for myISO in "${myBUILT[@]}"; do
    myOK+=("ok:$(fuSHORT "${myOUT}/${myISO}") ($(fuHUMAN "$(fuSIZE "${myOUT}/${myISO}")"))")
  done
  myITEMS=("${myOK[@]}" "${myITEMS[@]}" "info:T-Pot from ${myREPO_URL} at ${myBRANCH}")
  if [ "${myRC}" -ne 0 ];
    then
      myITEMS+=("info:The log: ${myLOG}")
      fuUI_SUMMARY "T-Pot ISO" "${myITEMS[@]}" || true
      return "${myRC}"
  fi
  command -v sha256sum >/dev/null || myCHECK="shasum -a 256 -c"
  if [ -n "${myRELEASE}" ];
    then
      myITEMS+=("ok:Each with .sha256, .sha512 and .buildinfo")
      if [ -n "${mySIGN_FPR}" ];
        then myITEMS+=("ok:SHA256SUMS and SHA512SUMS, signed by ${mySIGN_FPR} (.asc)")
        else myITEMS+=("ok:SHA256SUMS and SHA512SUMS (not signed)")
      fi
      if [ -n "${myUPLOAD_TAG}" ];
        then myITEMS+=("ok:Uploaded: https://github.com/${myGH_REPO}/releases/tag/${myUPLOAD_TAG}")
        else
          while IFS= read -r myF; do myFILES+=("${myF#"${myOUT}/"}"); done < <(fuRELEASE_FILES)
          myGH=$(fuGH_REPO_OF "${myREPO_URL}") && myGH=" --repo ${myGH}"
          myITEMS+=("next:Upload: cd $(fuQ "${myOUT}") && gh release upload <tag> ${myFILES[*]} --clobber${myGH}")
      fi
      myITEMS+=("next:Check: cd $(fuQ "${myOUT}") && ${myCHECK} SHA256SUMS")
      [ -z "${mySIGN_FPR}" ] || myITEMS+=("next:Check the signature: gpg --verify SHA256SUMS.asc SHA256SUMS")
      fuUI_SUMMARY "T-Pot ISOs of release ${myVERSION}" "${myITEMS[@]}"
      return
  fi
  myBOOT="BIOS or UEFI"
  [ "${myARCH}" != "arm64" ] || myBOOT="UEFI"
  [ "${myARCH}" != "both" ] || myBOOT="amd64: BIOS or UEFI, arm64: UEFI"
  myITEMS+=("info:Each with .sha256, .sha512 and .buildinfo next to it; the log: $(fuSHORT "${myLOG}")")
  if [ -z "${myDEVICE}" ]; then
    myITEMS+=("next:Onto a USB stick: $(fuQ "${mySELF}") -w $(fuQ "${myOUT}/${myBUILT[0]}") (or the menu); Windows: Rufus (DD mode) or balenaEtcher")
  fi
  myITEMS+=("next:Boot from it (${myBOOT}), choose Install T-Pot; the T-Pot installer starts at the first login")
  fuUI_SUMMARY "T-Pot ISO" "${myITEMS[@]}"
}

fuSHORT () {
  # a path in the checkout without it (installer/iso/out/...), others as they are
  case "$1" in "${myREPO}"/*) echo "${1#"${myREPO}/"}" ;; *) echo "$1" ;; esac
}

fuISO_FILES () {
  # the ISOs built here (installer/iso/out and its release folders), the newest first
  local myF
  for myF in "${myDIR}"/out/*.iso "${myDIR}"/out/release-*/*.iso; do
    [ -f "${myF}" ] && echo "$(date -r "${myF}" +%s 2>/dev/null || echo 0) ${myF}"
  done | sort -rn | cut -d ' ' -f 2-
}

fuISO_LABEL () {
  # fuISO_LABEL <iso>: its name, size and date for a list
  echo "${1##*/} ($(fuHUMAN "$(fuSIZE "$1")"), $(date -r "$1" '+%Y-%m-%d %H:%M' 2>/dev/null))"
}

fuMENU_STICK () {
  # fuMENU_STICK <iso>: the USB stick into myDEVICE, picked from those it may write to; rc 1 for back
  local myWHAT myLINE
  local -a myITEMS=() myDEVS=()
  while true; do
    myITEMS=() myDEVS=()
    while IFS= read -r myLINE; do
      [ -n "${myLINE}" ] || continue
      myDEVS+=("${myLINE%%|*}")
      myITEMS+=("${myLINE##*|}:${#myDEVS[@]}")
    done < <(fuUSB_LIST "$(dirname -- "$1")")
    [ "${#myDEVS[@]}" -gt 0 ] || fuUI_WARN "No USB stick or SD card found: plug one in and look again."
    fuASK myWHAT fuUI_CHOOSE "Which USB stick? Everything on it is erased." \
      ${myITEMS[@]+"${myITEMS[@]}"} "Look again:rescan" "Back:back"
    case "${myWHAT}" in
      back) return 1 ;;
      rescan) ;;
      *) myDEVICE="${myDEVS[myWHAT - 1]}"; return 0 ;;
    esac
  done
}

fuPICK_WRITE () {
  # fuPICK_WRITE <iso>: the ISO checked, the stick picked (unless -d), what is erased confirmed
  # (unless -y); then fuWRITE. rc 0 also for nothing written (asked: no)
  local myLINE
  fuISO_CHECK "$1" || return $?
  if [ -z "${myDEVICE}" ]; then
    fuMENU_STICK "$1" || { fuUI_INFO "Nothing was written."; return 0; }
  fi
  if ! myLINE=$(fuUSB_FIND "${myDEVICE}" "$(dirname -- "$1")");
    then
      fuUI_ERROR "${myDEVICE} is no USB stick or SD card it may write to (any more)."
      return 1
  fi
  if [ -z "${myYES}" ]; then
    fuCONFIRM_WRITE "$1" "${myLINE}" || { fuUI_INFO "Nothing was written."; return 0; }
  fi
  fuWRITE "$1" "${myDEVICE}"
}

fuBRANCHES () {
  # fuBRANCHES <repository> <file>: the branches of the repository into the file, one per line, master
  # and dev first, the rest sorted, names start.sh of the ISO would not take left out; the file stays
  # empty if it cannot be reached (30 seconds at most). rc 0 either way, fuUI_SPIN says nothing then
  local myLS="" myB
  : > "$2"
  myLS=$(GIT_TERMINAL_PROMPT=0 fuTIMEOUT 30 git ls-remote --heads "$1" 2>/dev/null) || return 0
  awk '{ sub("^refs/heads/", "", $2); print $2 }' <<< "${myLS}" | sort | while IFS= read -r myB; do
    fuVALID_BRANCH "${myB}" && echo "${myB}"
  done | awk '$0 == "master" { m = 1; next } $0 == "dev" { d = 1; next } { rest[++n] = $0 }
              END { if (m) print "master"; if (d) print "dev"; for (i = 1; i <= n; i++) print rest[i] }' > "$2"
}

fuMENU_SOURCE () {
  # where the ISO installs T-Pot from: a branch of the repository (its list, the one now selected), a
  # tag or a commit (with the warning that update.sh cannot follow it), another repository and its
  # branches; rc 1 for back
  local myWHAT myIN myURL="${myREPO_URL}" myB myFOUND myHEADER
  local -a myITEMS myLABELS
  while true; do
    myITEMS=() myFOUND=""
    myPEEK_FILE=$(mktemp "${TMPDIR:-/tmp}/tpot-iso-branches.XXXXXX") || return 1
    if fuUI_SPIN "Asking ${myURL} for its branches ..." /dev/null fuBRANCHES "${myURL}" "${myPEEK_FILE}" \
       && [ -s "${myPEEK_FILE}" ];
      then
        myHEADER="Where does the ISO install T-Pot from? The branches of ${myURL}:"
        while IFS= read -r myB; do
          case "${myB}" in
            master) myITEMS+=("master (the release):=${myB}") ;;
            dev) myITEMS+=("dev (development, the next release):=${myB}") ;;
            *) myITEMS+=("${myB}:=${myB}") ;;
          esac
          [ "${myB}" != "${myBRANCH}" ] || [ "${myURL}" != "${myREPO_URL}" ] || myFOUND=1
        done < "${myPEEK_FILE}"
      else
        fuUI_WARN "${myURL} cannot be reached or has no branches: its branches are not listed."
        myHEADER="Where does the ISO install T-Pot from?"
        myITEMS+=("Type a branch name:name")
    fi
    rm -f "${myPEEK_FILE}"
    myPEEK_FILE=""
    # the one selected now, e.g. a tag, or a branch the list does not show
    [ -n "${myFOUND}" ] || [ "${myURL}" != "${myREPO_URL}" ] || myITEMS=("${myBRANCH} (as now):=${myBRANCH}" "${myITEMS[@]}")
    myITEMS+=("A tag or a commit (update.sh cannot update it):tag" "Another repository (now ${myURL}):repo" "Back:back")
    if [ -n "${myUI_GUM}" ] && [ -t 0 ];
      then
        myLABELS=()
        for myB in "${myITEMS[@]}"; do
          # gum filter ignores --selected for its single-choice cursor: Enter keeps the first item.
          if [ "${myB##*:}" = "=${myBRANCH}" ];
            then myLABELS=("${myB%:*}" "${myLABELS[@]}")
            else myLABELS+=("${myB%:*}")
          fi
        done
        # shellcheck disable=SC2154 # colours set by fuUI_INIT in installer/lib/ui.sh
        fuASK myIN fuUI_GUM filter --limit 1 --header "${myHEADER}" \
          --header.foreground "${myUI_GLASS}" --indicator.foreground "${myUI_MAGENTA}" \
          --match.foreground "${myUI_MAGENTA}" --selected-indicator.foreground "${myUI_MAGENTA}" \
          --prompt.foreground "${myUI_MAGENTA}" -- "${myLABELS[@]}"
        myWHAT=""
        for myB in "${myITEMS[@]}"; do
          if [ "${myB%:*}" = "${myIN}" ]; then myWHAT="${myB##*:}"; break; fi
        done
        [ -n "${myWHAT}" ] || { fuUI_ERROR "No source selected."; return 1; }
      else
        fuASK myWHAT fuUI_CHOOSE --selected "=${myBRANCH}" "${myHEADER}" "${myITEMS[@]}"
    fi
    case "${myWHAT}" in
      back) return 1 ;;
      =*) myBRANCH="${myWHAT#=}" myREPO_URL="${myURL}"; return 0 ;;
      repo)
        while true; do
          fuASK myIN fuUI_INPUT "Repository URL (enter: ${myURL}):"
          [ -n "${myIN}" ] || myIN="${myURL}"
          myIN=$(fuNORMALIZE_REPO "${myIN}")
          fuVALID_REPO "${myIN}" && break
          fuUI_WARN "Not a repository URL: ${myIN}"
        done
        myURL="${myIN}"
        continue ;;
    esac
    # name (a branch typed in), tag: a name of your own
    while true; do
      if [ "${myWHAT}" = "tag" ];
        then fuASK myIN fuUI_INPUT "Tag or commit (40 hex digits):"
        else fuASK myIN fuUI_INPUT "Branch (enter: ${myBRANCH}):"
      fi
      [ -n "${myIN}" ] || [ "${myWHAT}" = "tag" ] || myIN="${myBRANCH}"
      [ -n "${myIN}" ] && fuVALID_BRANCH "${myIN}" && break
      fuUI_WARN "Not a branch, tag or commit: ${myIN}"
    done
    [ "${myWHAT}" != "tag" ] || fuUI_WARN "A T-Pot of a tag or a commit is not on a branch, update.sh cannot update it (${myUPDATE_BACK})."
    myBRANCH="${myIN}" myREPO_URL="${myURL}"
    return 0
  done
}

fuMENU_ARCH () {
  # fuMENU_ARCH <default>: the architecture into myARCH and myARCHS; rc 1 for back
  local myWHAT
  fuASK myWHAT fuUI_CHOOSE --selected "$1" "For which machines?" \
    "amd64: PCs, servers and VMs (BIOS or UEFI):amd64" \
    "arm64: ARM servers and VMs (UEFI; for a Raspberry Pi see T-Pot Mobile):arm64" \
    "Both:both" "Back:back"
  [ "${myWHAT}" != "back" ] || return 1
  myARCH="${myWHAT}"
  if [ "${myARCH}" = "both" ]; then myARCHS=(amd64 arm64); else myARCHS=("${myARCH}"); fi
}

fuPEEK () {
  # fuPEEK <file>: what a build would get, into the file: fuREMOTE (2 lines), then per architecture
  # arch|netinst of now|cached, new, download or unknown|the one in the cache. Debian's SHA512SUMS
  # unsigned here (the build checks the signature), 15 seconds at most
  local myA myNAME myF myOLD myS
  fuREMOTE
  printf '%s\n%s\n' "${myREMOTE}" "${myREMOTE_TEXT}" > "$1"
  for myA in "${myARCHS[@]}"; do
    myNAME=$(curl -fsSL --max-time 15 "${myDEBIAN_CD}/debian-cd/current/${myA}/iso-cd/SHA512SUMS" 2>/dev/null \
             | awk -v arch="${myA}" '$2 ~ ("^debian-[0-9.]+-" arch "-netinst\\.iso$") { print $2; exit }')
    myOLD=""
    for myF in "${myCACHE}"/debian-*-"${myA}"-netinst.iso; do [ ! -f "${myF}" ] || myOLD="${myF##*/}"; done
    if [ -z "${myNAME}" ]; then myS="unknown"
    elif [ -f "${myCACHE}/${myNAME}" ]; then myS="cached"
    elif [ -n "${myOLD}" ]; then myS="new"
    else myS="download"
    fi
    echo "${myA}|${myNAME}|${myS}|${myOLD}" >> "$1"
  done
}

fuMENU_READY () {
  # fuMENU_READY <title> [item] ...: what the build gets (fuPEEK), the summary, Build or Back; rc 0
  # to build
  local myTITLE="$1" myR myRT myA myNAME myS myOLD myWHAT
  local -a myITEMS=()
  shift
  myPEEK_FILE=$(mktemp "${TMPDIR:-/tmp}/tpot-iso-peek.XXXXXX") || return 1
  fuUI_SPIN "Asking Debian and ${myREPO_URL} ..." /dev/null fuPEEK "${myPEEK_FILE}" || true
  { read -r myR; read -r myRT; } < "${myPEEK_FILE}"
  myITEMS+=("info:$(fuDESCRIBE)")
  case "${myR}" in
    ok) myITEMS+=("ok:${myRT}") ;;
    "") ;;
    *) myITEMS+=("warn:${myRT}") ;;
  esac
  if [ -n "${myINPUT}" ];
    then myITEMS+=("info:Debian netinst: ${myINPUT}")
    else
      while IFS='|' read -r myA myNAME myS myOLD; do
        case "${myS}" in
          cached) myITEMS+=("ok:Debian netinst ${myNAME}: in the cache") ;;
          new) myITEMS+=("info:New Debian netinst ${myNAME}: downloaded, replaces ${myOLD} in the cache") ;;
          download) myITEMS+=("info:Debian netinst ${myNAME}: downloaded into the cache") ;;
          *) myITEMS+=("warn:Debian cannot be reached now: the build asks again for the netinst for ${myA}") ;;
        esac
      done < <(tail -n +3 "${myPEEK_FILE}")
  fi
  rm -f "${myPEEK_FILE}"
  myPEEK_FILE=""
  myITEMS+=("info:${myHOW_TEXT}" "$@" "next:The same without the menu: $(fuCOMMAND_LINE)")
  fuUI_SUMMARY "${myTITLE}" "${myITEMS[@]}" || true
  fuASK myWHAT fuUI_CHOOSE "Build now?" "Build:build" "Back:back"
  [ "${myWHAT}" = "build" ]
}

fuMENU_BUILD () {
  # architecture, source, the summary; rc 0 to build, 1 for back
  fuMENU_ARCH "$(fuDEF_ARCH)" || return 1
  fuMENU_SOURCE || return 1
  fuHOW || return 1
  fuMENU_READY "Ready to build"
}

fuMENU_RELEASE () {
  # the checks of a release, architecture, key, upload, the summary; rc 0 to build, 1 for back
  local myWHAT myLINE myOLD myTAG
  local -a myITEMS=() myKEYS=()
  myRELEASE=1
  fuUI_INFO "Checking the checkout and ${myREPO_URL} ..."
  fuRELEASE_CHECK || return 1
  fuHOW || return 1
  fuMENU_ARCH both || return 1
  while IFS= read -r myLINE; do
    [ -n "${myLINE}" ] && myKEYS+=("${myLINE#*|} (${myLINE%%|*}):${myLINE%%|*}")
  done < <(fuGPG_KEYS)
  if [ "${#myKEYS[@]}" -gt 0 ];
    then
      fuASK myWHAT fuUI_CHOOSE --selected "${TPOT_ISO_SIGN_KEY:-${myKEYS[0]##*:}}" \
        "Sign SHA256SUMS and SHA512SUMS with GPG?" "${myKEYS[@]}" "Do not sign:none"
      [ "${myWHAT}" = "none" ] || mySIGN_FPR="${myWHAT}"
    else fuUI_INFO "No GPG key to sign with (gpg --list-secret-keys): SHA256SUMS and SHA512SUMS are not signed."
  fi
  if fuYES --default no "Upload the files to a GitHub release?" "Upload" "No"; then
    while true; do
      fuASK myTAG fuUI_INPUT "The tag of the release (enter: ${myVERSION}):"
      [ -n "${myTAG}" ] || myTAG="${myVERSION}"
      fuVALID_BRANCH "${myTAG}" && break
      fuUI_WARN "Not a tag: ${myTAG}"
    done
    myUPLOAD_TAG="${myTAG}"
    fuUPLOAD_CHECK || return 1
  fi
  fuOUT_DEFAULT
  myITEMS=("info:Into $(fuSHORT "${myOUT}"): tpot-${myVERSION}-debian-<x.y.z>-<arch>.iso, .sha256, .sha512, .buildinfo, SHA256SUMS, SHA512SUMS")
  if [ -n "${mySIGN_FPR}" ];
    then myITEMS+=("info:Signed by ${mySIGN_FPR}")
    else myITEMS+=("info:Not signed")
  fi
  if [ -n "${myUPLOAD_TAG}" ];
    then myITEMS+=("info:Uploaded to https://github.com/${myGH_REPO}/releases/tag/${myUPLOAD_TAG}")
    else myITEMS+=("info:Not uploaded")
  fi
  myOLD=$(fuRELEASE_OLD)
  [ -z "${myOLD}" ] || myITEMS+=("warn:Replaces the files of an earlier build there: ${myOLD}")
  fuMENU_READY "Ready to build the ISOs of release ${myVERSION}" "${myITEMS[@]}"
}

fuMENU_WRITE () {
  # the ISO into myWRITE_ISO: one built here (the newest first) or another file; rc 1 for back
  local myWHAT myIN myF
  local -a myISOS=() myITEMS=()
  while IFS= read -r myF; do [ -n "${myF}" ] && myISOS+=("${myF}"); done < <(fuISO_FILES)
  for myF in "${!myISOS[@]}"; do myITEMS+=("$(fuISO_LABEL "${myISOS[myF]}"):$((myF + 1))"); done
  [ "${#myISOS[@]}" -gt 0 ] || fuUI_INFO "No ISO in $(fuSHORT "${myDIR}/out") yet: build one first, or take another file."
  fuASK myWHAT fuUI_CHOOSE "Which ISO?" ${myITEMS[@]+"${myITEMS[@]}"} "Another file:other" "Back:back"
  case "${myWHAT}" in
    back) return 1 ;;
    other)
      while true; do
        fuASK myIN fuUI_INPUT "The path of the ISO (enter: back):"
        [ -n "${myIN}" ] || return 1
        # a path typed with ~
        [[ "${myIN}" != \~/* ]] || myIN="${HOME}/${myIN:2}"
        [ -f "${myIN}" ] && break
        fuUI_WARN "No such file: ${myIN}"
      done
      myWRITE_ISO="$(CDPATH="" cd -- "$(dirname -- "${myIN}")" && pwd)/$(basename -- "${myIN}")" ;;
    *) myWRITE_ISO="${myISOS[myWHAT - 1]}" ;;
  esac
}

fuMENU_AFTER () {
  # after a build of the menu: an ISO onto a USB stick now; rc of the write, 0 without one
  local myWHAT myI
  local -a myITEMS=()
  [ "${#myBUILT[@]}" -gt 0 ] || return 0
  fuYES --default no "Write it to a USB stick now?" "Write" "Done" || return 0
  myWRITE_ISO="${myOUT}/${myBUILT[0]}"
  if [ "${#myBUILT[@]}" -gt 1 ]; then
    for myI in "${!myBUILT[@]}"; do myITEMS+=("${myBUILT[myI]}:$((myI + 1))"); done
    fuASK myWHAT fuUI_CHOOSE "Which ISO?" "${myITEMS[@]}"
    myWRITE_ISO="${myOUT}/${myBUILT[myWHAT - 1]}"
  fi
  fuWLOG
  fuPICK_WRITE "${myWRITE_ISO}"
}

fuMENU () {
  local myWHAT myRC=0 myB0="${myBRANCH}" myR0="${myREPO_URL}"
  myMENU=1
  fuUI_BANNER "ISO Builder (preview)" \
    "The Debian ${myDEBIAN_MAJOR} netinst with the T-Pot installer: build it, write it to a USB stick." \
    "T-Pot $(fuUI_VERSION "${myREPO}") from ${myREPO_URL} at ${myBRANCH}"
  while true; do
    # back in the menu: what an earlier choice set goes
    myRELEASE="" mySIGN_FPR="" myUPLOAD_TAG="" myGH_REPO="" myDEVICE="" myWRITE_ISO="" myARCH=""
    myBRANCH="${myB0}" myREPO_URL="${myR0}"
    [ -n "${myOUT_GIVEN}" ] || myOUT=""
    fuASK myWHAT fuUI_CHOOSE "What do you want to do?" "Build an ISO:build" "Write an ISO to a USB stick:write" \
      "Build the ISOs of a release with checksums (maintainers):release" "Quit:quit"
    case "${myWHAT}" in
      quit) return 0 ;;
      build)
        if fuMENU_BUILD; then
          fuRUN || return $?
          fuMENU_AFTER
          return $?
        fi ;;
      write)
        if fuMENU_WRITE; then
          fuWLOG
          fuPICK_WRITE "${myWRITE_ISO}"
          return $?
        fi ;;
      release)
        if fuMENU_RELEASE; then
          fuRUN
          return $?
        fi ;;
    esac
  done
}

fuCLI_WRITE () {
  # -w: the ISO onto a USB stick
  fuWLOG
  fuUI_BANNER "ISO Builder (preview)" "Writing ${myWRITE_ISO##*/} to a USB stick"
  fuPICK_WRITE "${myWRITE_ISO}"
}

fuCLI_BUILD () {
  # a build without the menu; with -d the stick is confirmed first and written after the build
  local myLINE
  fuUI_BANNER "ISO Builder (preview)" "$(fuDESCRIBE)"
  if [ -n "${myDEVICE}" ] && [ -z "${myYES}" ]; then
    fuHOW || return 1
    if ! myLINE=$(fuUSB_FIND "${myDEVICE}");
      then fuUI_ERROR "${myDEVICE} is no USB stick or SD card it may write to (any more)."; return 1
    fi
    fuUI_SUMMARY "Build and write" "info:$(fuDESCRIBE)" "info:Then onto ${myLINE##*|}" \
      "warn:Everything on it is erased: all its partitions and files" || true
    fuYES --default no "Build, then erase ${myLINE##*|} and write the ISO?" "Build and write" "Cancel" \
      || { fuUI_INFO "Nothing was built or written."; return 0; }
  fi
  fuRUN || return $?
  [ -n "${myDEVICE}" ] || return 0
  fuWLOG
  fuWRITE "${myOUT}/${myBUILT[0]}" "${myDEVICE}"
}

fuTERMINAL () {
  [ -t 0 ] && [ -t 1 ]
}

fuPARSE () {
  # the options into the my* variables and myACTION (help, list, write, menu, build); rc 1 (exit 2)
  # for wrong ones, nothing is made then
  local myOPT myN="$#" mySEEN="" myLINE
  local OPTIND=1
  while getopts ":a:b:r:i:o:DhyRs:U:w:d:l" myOPT; do
    mySEEN="${mySEEN}${myOPT}"
    case "${myOPT}" in
      a) myARCH="${OPTARG}" ;;
      b) myBRANCH="${OPTARG}" ;;
      r) myREPO_URL="${OPTARG}" ;;
      i) myINPUT="${OPTARG}" ;;
      o) myOUT="${OPTARG}" myOUT_GIVEN=1 ;;
      D) myDOCKER=1 ;;
      y) myYES=1 ;;
      R) myRELEASE=1 ;;
      s) mySIGN_KEY="${OPTARG}" ;;
      U) myUPLOAD_TAG="${OPTARG}" ;;
      w) myWRITE_ISO="${OPTARG}" ;;
      d) myDEVICE="${OPTARG}" ;;
      l) myACTION="list" ;;
      h) myACTION="help"; return 0 ;;
      :) fuUI_USAGE_ERROR "-${OPTARG} needs a value." "${mySELF##*/}"; return 1 ;;
      *) fuUI_USAGE_ERROR "Unknown option: -${OPTARG}" "${mySELF##*/}"; return 1 ;;
    esac
  done
  shift $((OPTIND - 1))
  [ "$#" -eq 0 ] || { fuUI_USAGE_ERROR "Unknown argument: $1" "${mySELF##*/}"; return 1; }
  if [ "${myACTION}" = "list" ]; then
    [ "${mySEEN}" = "l" ] || { fuUI_USAGE_ERROR "-l goes alone." "${mySELF##*/}"; return 1; }
    return 0
  fi
  if [ -n "${myDEVICE}" ] && ! fuTERMINAL && [ -z "${myYES}" ]; then
    fuUI_USAGE_ERROR "Without a terminal -d needs -y: it confirms erasing the stick." "${mySELF##*/}"
    return 1
  fi
  if [ -n "${myWRITE_ISO}" ]; then
    if [[ "${mySEEN//[wdy]/}" != "" ]]; then
      fuUI_USAGE_ERROR "-w takes only -d and -y." "${mySELF##*/}"
      return 1
    fi
    [ -f "${myWRITE_ISO}" ] || { fuUI_USAGE_ERROR "No such file: ${myWRITE_ISO}" "${mySELF##*/}"; return 1; }
    if [ -z "${myDEVICE}" ] && ! fuTERMINAL; then
      fuUI_USAGE_ERROR "Without a terminal -w needs -d (installer/iso/build.sh -l lists the sticks)." "${mySELF##*/}"
      return 1
    fi
    myWRITE_ISO="$(CDPATH="" cd -- "$(dirname -- "${myWRITE_ISO}")" && pwd)/$(basename -- "${myWRITE_ISO}")"
    if [ -n "${myDEVICE}" ] && ! myLINE=$(fuUSB_FIND "${myDEVICE}" "$(dirname -- "${myWRITE_ISO}")"); then
      fuUI_USAGE_ERROR "${myDEVICE} is no USB stick or SD card it may write to (installer/iso/build.sh -l lists them)." "${mySELF##*/}"
      return 1
    fi
    [ -z "${myDEVICE}" ] || myDEVICE="${myLINE%%|*}"
    myACTION="write"
    return 0
  fi
  if [ -z "${myRELEASE}" ] && [ -n "${mySIGN_KEY}${myUPLOAD_TAG}" ]; then
    fuUI_USAGE_ERROR "-s and -U go with -R only." "${mySELF##*/}"
    return 1
  fi
  if [ -z "${myARCH}" ]; then
    myARCH=$(fuDEF_ARCH)
    [ -n "${myARCH}" ] || { fuUI_USAGE_ERROR "This machine is neither x86-64 nor arm64, -a says which ISO." "${mySELF##*/}"; return 1; }
  fi
  case "${myARCH}" in
    amd64|arm64) myARCHS=("${myARCH}") ;;
    both) myARCHS=(amd64 arm64) ;;
    *) fuUI_USAGE_ERROR "Unknown architecture: ${myARCH} (amd64, arm64 or both)." "${mySELF##*/}"; return 1 ;;
  esac
  if [ -n "${myDEVICE}" ] && { [ -n "${myRELEASE}" ] || [ "${myARCH}" = "both" ]; }; then
    fuUI_USAGE_ERROR "-d writes the ISO of one architecture, not with -R or -a both." "${mySELF##*/}"
    return 1
  fi
  if [ -n "${myINPUT}" ]; then
    [ "${myARCH}" != "both" ] || { fuUI_USAGE_ERROR "-i is the netinst of one architecture, not with -a both." "${mySELF##*/}"; return 1; }
    [ -f "${myINPUT}" ] || { fuUI_USAGE_ERROR "No such file: ${myINPUT}" "${mySELF##*/}"; return 1; }
    [[ "$(basename -- "${myINPUT}")" =~ ^debian-${myDEBIAN_MAJOR}\.[0-9]+(\.[0-9]+)?-${myARCH}-netinst\.iso$ ]] \
      || { fuUI_USAGE_ERROR "Not a Debian ${myDEBIAN_MAJOR} netinst for ${myARCH} by its name (debian-${myDEBIAN_MAJOR}.x.y-${myARCH}-netinst.iso): ${myINPUT}" "${mySELF##*/}"; return 1; }
    myINPUT="$(CDPATH="" cd -- "$(dirname -- "${myINPUT}")" && pwd)/$(basename -- "${myINPUT}")"
  fi
  fuSOURCE || return 1
  if [ -n "${myUPLOAD_TAG}" ] && ! fuVALID_BRANCH "${myUPLOAD_TAG}"; then
    fuUI_USAGE_ERROR "Not a tag: ${myUPLOAD_TAG}" "${mySELF##*/}"
    return 1
  fi
  if [ -n "${myDEVICE}" ]; then
    if ! myLINE=$(fuUSB_FIND "${myDEVICE}" ${myOUT:+"${myOUT}"}); then
      fuUI_USAGE_ERROR "${myDEVICE} is no USB stick or SD card it may write to (installer/iso/build.sh -l lists them)." "${mySELF##*/}"
      return 1
    fi
    myDEVICE="${myLINE%%|*}"
  fi
  if [ "${myN}" -eq 0 ] && fuTERMINAL; then myACTION="menu"; else myACTION="build"; fi
  return 0
}

fuMAIN () {
  fuPARSE "$@" || return 2
  if [ "${myACTION}" = "help" ]; then fuUI_INIT; fuHELP; return 0; fi
  if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    fuUI_ERROR "The ISO builder needs bash 4 or newer (macOS: brew install bash)."
    return 1
  fi
  [ -z "${TPOT_ISO_IN_DOCKER:-}" ] || { fuIN_DOCKER; return $?; }
  fuGUM_MACOS
  fuUI_INIT
  # shellcheck disable=SC2034 # fuUI_BANNER of ui.sh shows the T-Pot logo with it
  myUI_LOGO=1
  trap fuEXIT EXIT
  trap fuCANCEL INT TERM
  case "${myACTION}" in
    list) fuLIST ;;
    write) fuCLI_WRITE ;;
    menu) fuMENU ;;
    *) fuCLI_BUILD ;;
  esac
}

# sourced (the tests): the functions only
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0
fuMAIN "$@"
exit $?
