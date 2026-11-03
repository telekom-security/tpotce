#!/usr/bin/env bash
# The T-Pot ISO at the end of the Debian installer (late.sh runs it in the new system, as root): the
# T-Pot of the ISO goes into the home folder of the user Debian created, as the T-Pot installer would
# leave it before its questions, so that it asks them at the first login without downloading:
#   ~/tpotce                             T-Pot (tpotce.tar: its .git, one commit as git clone leaves it)
#   ~/.local/share/tpotce/bin/gum        gum, its sha256 checked against ui.sh of that T-Pot
#   ~/.local/share/tpotce/venv           the T-Pot Manager (tpot setup with the wheels of the ISO)
#   /etc/ssh/sshd_config.d/tpot.conf     the colours of an SSH login, the line the playbook writes
# It never stops the Debian installer: what fails is left to the T-Pot installer at the first login
# (which needs the internet for it then), the log is /var/log/tpot-iso.log. rc 1 when ~/tpotce
# could not be made. Argument: the user (else the first member of the group sudo); for the tests
# TPOT_ISO_SHARE, TPOT_ISO_LOG and TPOT_ISO_SSHD.
unset CDPATH
mySHARE="${TPOT_ISO_SHARE:-/usr/local/share/tpot-iso}"
myLOG="${TPOT_ISO_LOG:-/var/log/tpot-iso.log}"
mySSHD="${TPOT_ISO_SSHD:-/etc/ssh/sshd_config.d}"
mySSHD_LINE="AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION"
exec >> "${myLOG}" 2>&1 < /dev/null
echo "T-Pot ISO: prepare.sh at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

fuCONF () {
  # fuCONF <key>: its value in iso.conf (read, never sourced)
  sed -n "s/^$1=//p" "${mySHARE}/iso.conf" 2>/dev/null | head -n 1
}

fuPIN () {
  # fuPIN <file> <name>: the value of name="value" in a script (read, never sourced)
  sed -n "s/^$2=\"\\([^\"]*\\)\"\$/\\1/p" "$1" 2>/dev/null | head -n 1
}

fuAS_USER () {
  # fuAS_USER <script> [arg ...]: bash as the user, in a plain environment of its own
  local mySCRIPT="$1"
  shift
  runuser -u "${myUSER}" -- env -i HOME="${myHOME}" USER="${myUSER}" LOGNAME="${myUSER}" PATH="${PATH}" \
    LANG=C.UTF-8 TPOT_GUM=off bash -c "${mySCRIPT}" tpot-iso "$@"
}

fuSSHD () {
  # the line of the playbook, unless sshd -t takes the config without it and not with it
  [ -d "${mySSHD}" ] && [ ! -e "${mySSHD}/tpot.conf" ] || return 0
  echo "${mySSHD_LINE}" > "${mySSHD}/tpot.conf" && chmod 0644 "${mySSHD}/tpot.conf" || return 0
  command -v sshd >/dev/null || return 0
  sshd -t && return 0
  mv -f "${mySSHD}/tpot.conf" "${mySSHD}/tpot.conf.tpot-iso"
  if sshd -t;
    then
      echo "T-Pot ISO: sshd does not take ${mySSHD_LINE}, left out."
      rm -f "${mySSHD}/tpot.conf.tpot-iso"
    else mv -f "${mySSHD}/tpot.conf.tpot-iso" "${mySSHD}/tpot.conf"
  fi
}

fuTPOT () {
  # ~/tpotce from tpotce.tar at the commit of iso.conf, nothing of it when that fails
  local myCOMMIT
  myCOMMIT=$(fuCONF TPOT_COMMIT)
  if [ -e "${myHOME}/tpotce" ];
    then
      echo "T-Pot ISO: ${myHOME}/tpotce exists, it stays as it is."
      return 0
  fi
  [ -f "${mySHARE}/tpotce.tar" ] && [ -n "${myCOMMIT}" ] || { echo "T-Pot ISO: the ISO carries no T-Pot."; return 1; }
  if fuAS_USER 'mkdir "${HOME}/tpotce" && tar -xf "$1" -C "${HOME}/tpotce" && git -C "${HOME}/tpotce" reset -q --hard \
                && [ "$(git -C "${HOME}/tpotce" rev-parse HEAD)" = "$2" ]' "${mySHARE}/tpotce.tar" "${myCOMMIT}";
    then
      echo "T-Pot ISO: ${myHOME}/tpotce is T-Pot ${myCOMMIT}."
      return 0
  fi
  echo "T-Pot ISO: ${myHOME}/tpotce could not be made from tpotce.tar."
  rm -rf "${myHOME}/tpotce"
  return 1
}

fuGUM () {
  # gum of the ISO where ui.sh looks for it, if its sha256 is the one of ui.sh of that T-Pot
  local myUI="${myHOME}/tpotce/installer/lib/ui.sh" myVER myM mySHA myNAME
  case "$(uname -m 2>/dev/null)" in
    x86_64|amd64) myM="x86_64" ;;
    aarch64|arm64) myM="arm64" ;;
    *) echo "T-Pot ISO: no gum for $(uname -m 2>/dev/null)."; return 0 ;;
  esac
  myVER=$(fuPIN "${myUI}" myUI_GUM_VERSION)
  mySHA=$(fuPIN "${myUI}" "myUI_GUM_SHA256_${myM}")
  myNAME="gum_${myVER}_Linux_${myM}"
  if [ -z "${myVER}" ] || [ -z "${mySHA}" ] || [ ! -f "${mySHARE}/${myNAME}.tar.gz" ];
    then
      echo "T-Pot ISO: the ISO carries no gum ${myVER:-?} for ${myM}."
      return 0
  fi
  if [ "$(sha256sum "${mySHARE}/${myNAME}.tar.gz" | awk '{ print $1 }')" != "${mySHA}" ];
    then
      echo "T-Pot ISO: ${myNAME}.tar.gz is not the gum of ui.sh (sha256), left out."
      return 0
  fi
  if fuAS_USER 'myBIN="${HOME}/.local/share/tpotce/bin" && mkdir -p "${myBIN}" && myTMP=$(mktemp -d) || exit 1
                tar -xzf "$1" -C "${myTMP}" "$2/gum" && install -m 0755 "${myTMP}/$2/gum" "${myBIN}/gum.tpot-iso" \
                  && mv -f "${myBIN}/gum.tpot-iso" "${myBIN}/gum"
                myRC=$?
                rm -rf "${myTMP}" "${myBIN}/gum.tpot-iso"
                exit "${myRC}"' "${mySHARE}/${myNAME}.tar.gz" "${myNAME}";
    then echo "T-Pot ISO: gum ${myVER} is in ${myHOME}/.local/share/tpotce/bin."
    else echo "T-Pot ISO: gum ${myVER} could not be put into ${myHOME}/.local/share/tpotce/bin."
  fi
}

fuMANAGER () {
  # the venv of the T-Pot Manager from the wheels of the ISO (tpot setup goes to PyPI for the rest)
  if [ ! -d "${mySHARE}/wheels" ];
    then
      echo "T-Pot ISO: the ISO carries no wheels, tpot setup comes at the first login."
      return 0
  fi
  if fuAS_USER '"${HOME}/tpotce/tpot" setup --wheels "$1"' "${mySHARE}/wheels";
    then echo "T-Pot ISO: the T-Pot Manager is set up."
    else echo "T-Pot ISO: tpot setup failed, the T-Pot installer runs it again at the first login."
  fi
}

fuSSHD
myUSER="${1:-}"
[ -n "${myUSER}" ] || myUSER=$(getent group sudo 2>/dev/null | cut -d: -f4 | cut -d, -f1)
myHOME=""
[ -z "${myUSER}" ] || myHOME=$(getent passwd "${myUSER}" 2>/dev/null | cut -d: -f6)
if [ -z "${myUSER}" ] || [ "${myUSER}" = "root" ] || [ -z "${myHOME}" ] || [ ! -d "${myHOME}" ];
  then
    echo "T-Pot ISO: no user with a home folder (${myUSER:-none}), T-Pot comes at the first login."
    exit 1
fi
myRC=0
if fuTPOT;
  then
    fuGUM
    fuMANAGER
  else myRC=1
fi
# what is in ~/tpotce now need not stay twice; the wheels stay for tpot setup of another branch
rm -f "${mySHARE}/tpotce.tar" "${mySHARE}"/gum_*_Linux_*.tar.gz
exit "${myRC}"
