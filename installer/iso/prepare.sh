#!/usr/bin/env bash
# The T-Pot ISO at the end of the Debian installer (late.sh runs it in the new system, as root): the
# T-Pot of the ISO goes into the home folder of the user Debian created, as the T-Pot installer would
# leave it before its questions, so that it asks them after the first boot without downloading:
#   ~/tpotce                             T-Pot (tpotce.tar: its .git, one commit as git clone leaves it)
#   ~/.local/share/tpotce/bin/gum        gum, its sha256 checked against ui.sh of that T-Pot
#   ~/.local/share/tpotce/venv           the T-Pot Manager (tpot setup with the wheels of the ISO)
#   /etc/ssh/sshd_config.d/tpot.conf     the colours of an SSH login, the line the playbook writes
#   /etc/systemd/system/tpot-iso.service.d/user.conf, multi-user.target.wants/tpot-iso.service
#                                        the T-Pot installer after the boot on tty8, as that user
# It never stops the Debian installer: what fails is left to the T-Pot installer when it starts
# (which needs the internet for it then), the log is /var/log/tpot-iso.log. rc 1 when ~/tpotce
# could not be made. Argument: the user (else the first member of the group sudo); for the tests
# TPOT_ISO_SHARE, TPOT_ISO_LOG, TPOT_ISO_SSHD, TPOT_ISO_SYSTEMD and TPOT_ISO_ISSUE.
# --complete reconciles the login hint and managed terminal after installation, also on update.
unset CDPATH
mySHARE="${TPOT_ISO_SHARE:-/usr/local/share/tpot-iso}"
myLOG="${TPOT_ISO_LOG:-/var/log/tpot-iso.log}"
mySSHD="${TPOT_ISO_SSHD:-/etc/ssh/sshd_config.d}"
mySYSTEMD="${TPOT_ISO_SYSTEMD:-/etc/systemd/system}"
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

fuSERVICE () {
  # tpot-iso.service (late.sh put it there) for the user: its drop-in and the link systemctl
  # enable would make (no systemctl in the Debian installer); never for the user tpot, which
  # start.sh refuses
  local myDROPIN="${mySYSTEMD}/tpot-iso.service.d"
  [ -f "${mySYSTEMD}/tpot-iso.service" ] || { echo "T-Pot ISO: no tpot-iso.service, the installer starts at a login."; return 0; }
  if [ "${myUSER}" = "tpot" ];
    then
      echo "T-Pot ISO: the user tpot is reserved for the containers, tpot-iso.service stays off."
      return 0
  fi
  if mkdir -p "${myDROPIN}" "${mySYSTEMD}/multi-user.target.wants" \
     && printf '%s\n' "# the user of the T-Pot ISO (prepare.sh)" "[Service]" "User=${myUSER}" > "${myDROPIN}/user.conf" \
     && chmod 0644 "${myDROPIN}/user.conf" \
     && ln -sfn "${mySYSTEMD}/tpot-iso.service" "${mySYSTEMD}/multi-user.target.wants/tpot-iso.service";
    then echo "T-Pot ISO: tpot-iso.service starts the T-Pot installer after the boot, as ${myUSER}."
    else echo "T-Pot ISO: tpot-iso.service could not be enabled, the installer starts at a login."
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
      echo "T-Pot ISO: the ISO carries no wheels, tpot setup comes when the installer starts."
      return 0
  fi
  if fuAS_USER '"${HOME}/tpotce/tpot" setup --wheels "$1"' "${mySHARE}/wheels";
    then echo "T-Pot ISO: the T-Pot Manager is set up."
    else echo "T-Pot ISO: tpot setup failed, the T-Pot installer runs it again when it starts."
  fi
}

fuGRUB () (
  local myGRUB="${TPOT_ISO_GRUB:-/etc/default/grub.d}"
  local myTHEME="${TPOT_ISO_THEME:-/usr/share/grub/themes/tpot}"
  local myCREATED="" myFILE
  [ -d "${mySHARE}/grub" ] || return 0
  if [ -e "${myGRUB}/90-tpot.cfg" ] || [ -L "${myGRUB}/90-tpot.cfg" ] \
     || [ -e "${myTHEME}" ] || [ -L "${myTHEME}" ]; then
    echo "T-Pot ISO: existing GRUB customization kept."
    return 0
  fi
  for myFILE in theme.txt splash.png hl_c.png tpot.cfg; do
    [ -f "${mySHARE}/grub/${myFILE}" ] || { echo "T-Pot ISO: missing GRUB payload ${myFILE}."; return 1; }
  done
  # Only paths created by this invocation are removed on failure.
  trap 'myRC=$?
        if [ "${myRC}" -ne 0 ] && [ -n "${myCREATED}" ]; then
          rm -f -- "${myGRUB}/90-tpot.cfg" "${myTHEME}/theme.txt" "${myTHEME}/splash.png" "${myTHEME}/hl_c.png" || exit 1
          rmdir -- "${myTHEME}" || exit 1
        fi
        exit "${myRC}"' EXIT
  mkdir -p "${myGRUB}" "$(dirname "${myTHEME}")" && mkdir "${myTHEME}" || return 1
  myCREATED=1
  for myFILE in theme.txt splash.png hl_c.png; do
    cp "${mySHARE}/grub/${myFILE}" "${myTHEME}/${myFILE}" && chmod 0644 "${myTHEME}/${myFILE}" || return 1
  done
  cp "${mySHARE}/grub/tpot.cfg" "${myGRUB}/90-tpot.cfg" && chmod 0644 "${myGRUB}/90-tpot.cfg" || return 1
  if update-grub; then
    echo "T-Pot ISO: installed GRUB uses T-Pot's name and artwork."
    return 0
  fi
  echo "T-Pot ISO: GRUB appearance could not be applied; restoring Debian's configuration."
  rm -- "${myGRUB}/90-tpot.cfg" || return 1
  update-grub || echo "T-Pot ISO: GRUB configuration could not be regenerated; review the boot configuration."
  return 1
)

fuCOMPLETE () {
  # the login hint of the ISO goes once T-Pot is installed, only as late.sh put it there: its sha256
  # in tpot.issue.sha256, for ISOs from before that record the hints they shipped
  local myISSUE="${TPOT_ISO_ISSUE:-/etc/issue.d/tpot.issue}" myRECORD="${mySHARE}/tpot.issue.sha256"
  local myKNOWN="" myHASH=""
  [ -f "${mySHARE}/iso.conf" ] && [ -f "${mySYSTEMD}/tpot.service" ] || return 0
  [ -e "${myISSUE}" ] || [ -L "${myISSUE}" ] || return 0
  if [ ! -e "${myRECORD}" ] && [ ! -L "${myRECORD}" ]; then
    myKNOWN="4a45f7b3987c735b4d37ab72b5a70df2cef97d6eb67efbe6cf5b81022ed9d3f1
82491c88d922642f9ef68995a376a4bd20779e77b5fefa636877b529296f62f3
62d18d6ef994bff6b1b732865918a0e63e4a68b9acf543d9764853d335aa664f"
  elif [ -f "${myRECORD}" ] && [ ! -L "${myRECORD}" ]; then
    myKNOWN=$(cat "${myRECORD}") || myKNOWN=""
    [[ "${myKNOWN}" =~ ^[0-9a-f]{64}$ ]] || myKNOWN=""
  fi
  if [ -z "${myKNOWN}" ]; then
    echo "T-Pot ISO: the record of the ISO login hint ${myRECORD} is invalid; ${myISSUE} kept."
    return 1
  fi
  if [ ! -L "${myISSUE}" ] && [ -f "${myISSUE}" ]; then
    myHASH=$(sha256sum "${myISSUE}") || myHASH=""
  fi
  if [ -z "${myHASH}" ] || ! grep -qxF -- "${myHASH%% *}" <<< "${myKNOWN}"; then
    echo "T-Pot ISO: ${myISSUE} is not the unmodified T-Pot ISO hint; kept."
    return 1
  fi
  if ! rm -- "${myISSUE}"; then
    echo "T-Pot ISO: could not remove the obsolete ISO login hint ${myISSUE}."
    return 1
  fi
  echo "T-Pot ISO: obsolete ISO login hint removed."
}

fuINSTALLED () {
  # fuINSTALLED <package>: is it installed (dpkg's own state, not just known)?
  local mySTATE
  mySTATE=$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null) || return 1
  [ "${mySTATE:0:2}" = "ii" ]
}

fuTERMINAL_DEPS () {
  # fuTERMINAL_DEPS <new terminal.sh>: the Debian packages it names that are missing, without their
  # Recommends (no desktop comes along); rc 1 when it still cannot run, the caller keeps the old one
  local mySOURCE="$1" myPACKAGE myWHY="" myMISSING=() myLEFT=()
  local -a myPACKAGES
  read -r -a myPACKAGES < <(bash "${mySOURCE}" --packages) && [ "${#myPACKAGES[@]}" -gt 0 ] \
    || { echo "T-Pot ISO: the packages of the new terminal helper cannot be read; the previous terminal helper stays."; return 1; }
  for myPACKAGE in "${myPACKAGES[@]}"; do
    fuINSTALLED "${myPACKAGE}" || myMISSING+=("${myPACKAGE}")
  done
  if [ "${#myMISSING[@]}" -gt 0 ]; then
    echo "T-Pot ISO: installing the packages of the graphical terminal: ${myMISSING[*]}"
    # a stale or broken source must not stop it, the install below says whether it worked
    DEBIAN_FRONTEND=noninteractive apt-get -q -o DPkg::Lock::Timeout=300 update \
      || echo "T-Pot ISO: apt-get update failed, trying the install anyway."
    if ! DEBIAN_FRONTEND=noninteractive apt-get -q -y -o DPkg::Lock::Timeout=300 \
         install --no-install-recommends "${myMISSING[@]}"; then
      myWHY="they could not be installed"
    else
      for myPACKAGE in "${myMISSING[@]}"; do
        fuINSTALLED "${myPACKAGE}" || myLEFT+=("${myPACKAGE}")
      done
      [ "${#myLEFT[@]}" -eq 0 ] || myWHY="still not installed: ${myLEFT[*]}"
    fi
  fi
  if [ -z "${myWHY}" ] && ! myWHY=$(bash "${mySOURCE}" --ready); then
    myWHY="${myWHY:-a part} is missing"
  fi
  [ -n "${myWHY}" ] || return 0
  echo "T-Pot ISO: the graphical terminal needs ${myMISSING[*]:-${myPACKAGES[*]}}: ${myWHY}; the previous terminal helper stays."
  echo "T-Pot ISO: to finish it: sudo apt-get install --no-install-recommends ${myMISSING[*]:-${myPACKAGES[*]}}"
  echo "T-Pot ISO: then: sudo bash ${BASH_SOURCE[0]} --complete"
  return 1
}

fuTERMINAL () {
  local myTARGET="${mySHARE}/terminal.sh" myRECORD="${mySHARE}/terminal.sha256"
  local mySOURCE="${BASH_SOURCE[0]%/*}/terminal.sh" myKNOWN myHASH myNEW myTMP myMETA
  [ -f "${mySHARE}/iso.conf" ] && [ -f "${mySYSTEMD}/tpot.service" ] || return 0
  if [ -L "${myTARGET}" ] || [ -L "${myRECORD}" ] || [ ! -f "${myTARGET}" ] || [ ! -f "${mySOURCE}" ] \
     || { [ -e "${myRECORD}" ] && [ ! -f "${myRECORD}" ]; }; then
    echo "T-Pot ISO: terminal helper or ownership record is missing, non-regular or a link; kept."
    return 1
  fi
  if [ -f "${myRECORD}" ]; then
    myKNOWN=$(cat "${myRECORD}") || return 1
  else
    # The unmodified helper shipped before ISO payloads recorded its digest.
    myKNOWN="ad20ee0de7e7f6dcc6290eab7999937c92f7257ce724b900a63a03a3d10eb74e"
  fi
  myHASH=$(sha256sum "${myTARGET}") || return 1
  if [[ ! "${myKNOWN}" =~ ^[0-9a-f]{64}$ ]] || [ "${myHASH%% *}" != "${myKNOWN}" ]; then
    echo "T-Pot ISO: terminal helper was changed or its ownership record is invalid; kept."
    return 1
  fi
  # the managed one is current: nothing to migrate, a package removed on purpose stays removed
  myNEW=$(sha256sum "${mySOURCE}") || return 1
  [ "${myNEW%% *}" != "${myKNOWN}" ] || return 0
  fuTERMINAL_DEPS "${mySOURCE}" || return 1
  myTMP=$(mktemp "${mySHARE}/.terminal.XXXXXXXX") || return 1
  myMETA=$(mktemp "${mySHARE}/.terminal.XXXXXXXX") || { rm -f -- "${myTMP}"; return 1; }
  if install -m 0755 "${mySOURCE}" "${myTMP}" \
     && myHASH=$(sha256sum "${myTMP}") \
     && printf '%s\n' "${myHASH%% *}" > "${myMETA}" && chmod 0644 "${myMETA}" \
     && mv -f -- "${myTMP}" "${myTARGET}" && mv -f -- "${myMETA}" "${myRECORD}"; then
    echo "T-Pot ISO: managed terminal helper refreshed."
    return 0
  fi
  rm -f -- "${myTMP}" "${myMETA}" || echo "T-Pot ISO: terminal staging files could not be removed."
  echo "T-Pot ISO: terminal helper could not be refreshed."
  return 1
}

if [ "${1:-}" = "--complete" ]; then
  myRC=0
  fuCOMPLETE || myRC=1
  fuTERMINAL || myRC=1
  exit "${myRC}"
fi

myRC=0
fuGRUB || myRC=1
fuSSHD
myUSER="${1:-}"
[ -n "${myUSER}" ] || myUSER=$(getent group sudo 2>/dev/null | cut -d: -f4 | cut -d, -f1)
myHOME=""
[ -z "${myUSER}" ] || myHOME=$(getent passwd "${myUSER}" 2>/dev/null | cut -d: -f6)
if [ -z "${myUSER}" ] || [ "${myUSER}" = "root" ] || [ -z "${myHOME}" ] || [ ! -d "${myHOME}" ];
  then
    echo "T-Pot ISO: no user with a home folder (${myUSER:-none}), T-Pot comes at a login."
    exit 1
fi
fuSERVICE
if fuTPOT;
  then
    fuGUM
    fuMANAGER
  else myRC=1
fi
# what is in ~/tpotce now need not stay twice; the wheels stay for tpot setup of another branch
rm -f "${mySHARE}/tpotce.tar" "${mySHARE}"/gum_*_Linux_*.tar.gz
exit "${myRC}"
