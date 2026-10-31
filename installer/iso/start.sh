#!/usr/bin/env bash
# The T-Pot installer of a T-Pot ISO installation (/usr/local/share/tpot-iso/start.sh): runs the
# install.sh of the ISO with the repository and the branch the ISO was built for (iso.conf), the
# same installer as on a prepared Debian. At a terminal it asks first: Enter keeps that source, a
# branch (and a repository) of your own is for a test. Another source is cloned into ~/tpotce and
# its own install.sh runs, so a test gets the installer of its branch. The login hook
# (/etc/profile.d/tpot-iso.sh) starts it at the first login, by hand:
#   bash /usr/local/share/tpot-iso/start.sh [-b <branch>] [-r <repository>]

myDIR="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
myCONF="${myDIR}/iso.conf"
myDEST="${HOME}/tpotce"
myBACK="./update.sh -y -b master"
myARGS=("$@")

fuCONF () {
  # the value of $1 in iso.conf: KEY=VALUE lines, read and never sourced
  local myKEY myVALUE
  [ -r "${myCONF}" ] || return 1
  while IFS='=' read -r myKEY myVALUE; do
    if [ "${myKEY}" = "$1" ];
      then
        printf '%s\n' "${myVALUE}"
        return 0
    fi
  done < "${myCONF}"
  return 1
}

fuSTOP () {
  echo "T-Pot ISO: $1" >&2
  shift
  local myLINE
  for myLINE in "$@"; do echo "  ${myLINE}" >&2; done
  exit 1
}

fuUSAGE () {
  echo "T-Pot ISO: $1" >&2
  echo "  bash ${myDIR}/start.sh -h shows the options." >&2
  exit 2
}

fuVALID_REPO () {
  # no option, no space, no character a shell or git would read on its own (build.sh: the same)
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._~:/@%+=-]*$ ]]
}

fuVALID_BRANCH () {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/+-]*$ ]]
}

fuNORMALIZE_REPO () {
  # without a trailing slash or .git (install.sh: normalize_repo)
  local myURL="${1%/}"
  echo "${myURL%.git}"
}

fuHELP () {
  cat <<HELP
Usage: bash ${myDIR}/start.sh [-b <branch>] [-r <repository>]

Installs T-Pot on this Debian. Without an option it asks at a terminal; Enter installs the source
of this ISO: ${myISO_REPO} at ${myISO_BRANCH}.

  -b <branch>      another branch, e.g. to test it; it is cloned into ~/tpotce and its own
                   install.sh runs. A tag or a commit works too, update.sh cannot update it.
  -r <repository>  another repository (default: the one of this ISO)
  -h               this help
HELP
}

fuISO_SOURCE () {
  [ "${myBRANCH}" = "${myISO_BRANCH}" ] && [ "${myREPO}" = "${myISO_REPO}" ]
}

fuCHECK_SOURCE () {
  # does the repository have the branch? rc 0 a branch, 1 not to be had (said why), 2 a tag or a
  # commit (said so: update.sh cannot update it)
  local myRC=0
  command -v git >/dev/null \
    || fuSTOP "git is missing, it gets T-Pot from ${myREPO}." "sudo apt-get install git, then: bash ${myDIR}/start.sh -b ${myBRANCH} -r ${myREPO}"
  GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --heads "${myREPO}" "refs/heads/${myBRANCH}" >/dev/null 2>&1 || myRC=$?
  [ "${myRC}" -ne 0 ] || return 0
  if [ "${myRC}" -ne 2 ];
    then
      echo "  ${myREPO} cannot be reached (git ls-remote ended with ${myRC})." >&2
      return 1
  fi
  if [[ "${myBRANCH}" =~ ^[0-9a-f]{40}$ ]] \
     || GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --tags "${myREPO}" "refs/tags/${myBRANCH}" >/dev/null 2>&1;
    then
      echo "  ${myBRANCH} is a tag or a commit, no branch: T-Pot is installed from it, but update.sh cannot update it (later on a branch again: ${myBACK})."
      return 2
  fi
  echo "  ${myREPO} has no branch ${myBRANCH}." >&2
  return 1
}

fuASK () {
  # at a terminal: Enter keeps the source of the ISO, a branch of your own (and a repository) is
  # for a test; asks again until the repository has it
  local myIN myRC
  echo "T-Pot ISO: T-Pot is installed from ${myISO_REPO} at ${myISO_BRANCH}, the source of this ISO."
  echo "  Enter installs it. To test another branch, type its name (e.g. dev)."
  echo "  Ctrl+C stops, the installer asks again at the next login."
  while true; do
    myBRANCH="${myISO_BRANCH}" myREPO="${myISO_REPO}"
    read -r -p "  Branch [${myISO_BRANCH}]: " myIN || fuSTOP "Stopped, the installer asks again at the next login."
    [ -n "${myIN}" ] || return 0
    if ! fuVALID_BRANCH "${myIN}";
      then
        echo "  Not a branch, tag or commit: ${myIN}"
        continue
    fi
    myBRANCH="${myIN}"
    read -r -p "  Repository [${myISO_REPO}]: " myIN || fuSTOP "Stopped, the installer asks again at the next login."
    [ -z "${myIN}" ] || myREPO=$(fuNORMALIZE_REPO "${myIN}")
    if ! fuVALID_REPO "${myREPO}";
      then
        echo "  Not a repository URL: ${myREPO}"
        continue
    fi
    fuISO_SOURCE && return 0
    myRC=0
    fuCHECK_SOURCE || myRC=$?
    [ "${myRC}" -ne 0 ] || return 0
    if [ "${myRC}" -eq 2 ];
      then
        read -r -p "  Install it anyway? (y/N): " myIN || fuSTOP "Stopped, the installer asks again at the next login."
        case "${myIN}" in y|Y|yes|Yes|YES) return 0 ;; esac
    fi
    echo
  done
}

fuCLONE_AT () {
  # is ~/tpotce at the branch, the tag or the commit (install.sh: clone_matches)?
  local myHEAD myWANT
  [ "$(git -C "${myDEST}" rev-parse --abbrev-ref HEAD 2>/dev/null)" != "${myBRANCH}" ] || return 0
  myHEAD=$(git -C "${myDEST}" rev-parse HEAD 2>/dev/null)
  myWANT=$(git -C "${myDEST}" rev-parse -q --verify "${myBRANCH}^{commit}" 2>/dev/null)
  [ -n "${myWANT}" ] && [ "${myWANT}" = "${myHEAD}" ]
}

fuCLONE () {
  # another source: cloned into ~/tpotce, or the clone there if it is that source, and its own
  # install.sh, the installer of the branch under test
  local myAGAIN="bash ${myDIR}/start.sh -b ${myBRANCH} -r ${myREPO}"
  command -v git >/dev/null || fuSTOP "git is missing, it gets T-Pot from ${myREPO}." "sudo apt-get install git, then: ${myAGAIN}"
  if [ -e "${myDEST}" ];
    then
      if [ "$(fuNORMALIZE_REPO "$(git -C "${myDEST}" remote get-url origin 2>/dev/null)")" != "${myREPO}" ] || ! fuCLONE_AT;
        then
          fuSTOP "${myDEST} exists already and is not ${myREPO} at ${myBRANCH}." \
            "Remove it (rm -rf ${myDEST}), then: ${myAGAIN}"
      fi
      echo "T-Pot ISO: ${myDEST} is ${myREPO} at ${myBRANCH} already."
    else
      echo "T-Pot ISO: cloning ${myREPO} at ${myBRANCH} into ${myDEST} ..."
      if ! GIT_TERMINAL_PROMPT=0 git clone -q "${myREPO}" "${myDEST}" \
         || ! git -C "${myDEST}" -c advice.detachedHead=false checkout -q "${myBRANCH}";
        then
          rm -rf "${myDEST}"
          fuSTOP "Could not clone ${myREPO} at ${myBRANCH}." "Check the network and the name, then: ${myAGAIN}"
      fi
  fi
  [ -f "${myDEST}/install.sh" ] || fuSTOP "${myREPO} at ${myBRANCH} has no install.sh." "Remove ${myDEST} and pick another branch: bash ${myDIR}/start.sh"
  echo "T-Pot ISO: the T-Pot installer of ${myBRANCH} starts (T-Pot from ${myREPO} at ${myBRANCH})."
  echo "  Abort keeps a plain Debian, the installer asks again at the next login."
  echo "  Start it by hand: ${myAGAIN}"
  echo
  exec bash "${myDEST}/install.sh" -b "${myBRANCH}" -r "${myREPO}"
}

myISO_REPO="$(fuCONF TPOT_REPO_URL)"
myISO_BRANCH="$(fuCONF TPOT_BRANCH)"
myBRANCH=""
myREPO=""
while getopts ":b:r:h" myOPT; do
  case "${myOPT}" in
    b) myBRANCH="${OPTARG}" ;;
    r) myREPO=$(fuNORMALIZE_REPO "${OPTARG}") ;;
    h) fuHELP; exit 0 ;;
    :) fuUSAGE "Option -${OPTARG} requires an argument." ;;
    *) fuUSAGE "Unknown option -${OPTARG}." ;;
  esac
done
shift $((OPTIND - 1))
[ "$#" -eq 0 ] || fuUSAGE "Unknown argument: $1"

fuVALID_REPO "${myISO_REPO}" \
  || fuSTOP "${myCONF} has no valid TPOT_REPO_URL." "Run the installer by hand: ${myDIR}/install.sh -r <repository> -b <branch>"
fuVALID_BRANCH "${myISO_BRANCH}" \
  || fuSTOP "${myCONF} has no valid TPOT_BRANCH." "Run the installer by hand: ${myDIR}/install.sh -r <repository> -b <branch>"

if [ "$(id -u)" = "0" ];
  then
    fuSTOP "The T-Pot installer runs as the user created during the installation, not as root." \
      "Log in as that user and run: bash ${myDIR}/start.sh"
fi
# T-Pot runs its containers as the system user tpot (uid 2000): a login of that name would be
# turned into it
if [ "$(id -un)" = "tpot" ];
  then
    fuSTOP "The user tpot is reserved for the containers of T-Pot." \
      "Create another user with sudo (sudo adduser <name> && sudo usermod -aG sudo <name>)," \
      "log in as that user and run: bash ${myDIR}/start.sh"
fi

if [ -z "${TPOT_ISO_TERMINAL_TRIED:-}" ] && [ -t 0 ] && [ -t 1 ];
  then
    [ -r "${myDIR}/terminal.sh" ] || fuSTOP "${myDIR}/terminal.sh is missing."
    TPOT_ISO_TERMINAL_TRIED=1 exec bash "${myDIR}/terminal.sh" -- bash "${myDIR}/start.sh" "${myARGS[@]}"
fi

if [ -n "${myBRANCH}${myREPO}" ];
  then
    # the options: a tag or a commit named with -b is meant, the warning is enough
    [ -n "${myBRANCH}" ] || myBRANCH="${myISO_BRANCH}"
    [ -n "${myREPO}" ] || myREPO="${myISO_REPO}"
    fuVALID_BRANCH "${myBRANCH}" || fuUSAGE "Not a branch, tag or commit: ${myBRANCH}"
    fuVALID_REPO "${myREPO}" || fuUSAGE "Not a repository URL: ${myREPO}"
    if ! fuISO_SOURCE;
      then
        myRC=0
        fuCHECK_SOURCE || myRC=$?
        [ "${myRC}" -ne 1 ] || fuSTOP "T-Pot cannot be installed from ${myREPO} at ${myBRANCH}." "Name another one: bash ${myDIR}/start.sh -b <branch> [-r <repository>]"
    fi
elif [ -t 0 ];
  then
    fuASK
  else
    myBRANCH="${myISO_BRANCH}" myREPO="${myISO_REPO}"
fi

fuISO_SOURCE || fuCLONE
echo "T-Pot ISO: the T-Pot installer starts (T-Pot from ${myREPO} at ${myBRANCH})."
echo "  Abort keeps a plain Debian, the installer asks again at the next login."
echo "  Start it by hand: bash ${myDIR}/start.sh"
echo
exec bash "${myDIR}/install.sh" -b "${myBRANCH}" -r "${myREPO}"
