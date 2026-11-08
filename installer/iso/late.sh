#!/bin/sh
# The late_command of the T-Pot ISO (preseed.cfg): runs in the Debian installer (busybox sh) at
# its end and puts the T-Pot installer and the T-Pot of the ISO into the new system. Nothing is
# downloaded here; prepare.sh (in the new system, as root) sets up T-Pot for the user Debian
# created, the T-Pot installer starts after the first boot on the console (tpot-iso.service,
# start.sh), or at a login (profile.sh):
#   /usr/local/share/tpot-iso/   install.sh, start.sh, ui.sh, prepare.sh, iso.conf and version of
#                                the ISO, its T-Pot (tpotce.tar), gum and the wheels of the Manager,
#                                the sha256 of the login hint (tpot.issue.sha256) and of terminal.sh
#   /etc/systemd/system/tpot-iso.service
#                                starts the T-Pot installer after the boot on tty8 until T-Pot is
#                                installed (enabled by prepare.sh for its user)
#   /etc/profile.d/tpot-iso.sh   starts the T-Pot installer at a login until T-Pot is installed
#   /etc/issue.d/tpot.issue      the hint at the login prompt
# Arguments for the tests: the payload of the ISO (/cdrom/tpot) and the new system (/target).
set -e
mySRC="${1:-/cdrom/tpot}"
myTARGET="${2:-/target}"
myDEST="${myTARGET}/usr/local/share/tpot-iso"

for myFILE in install.sh start.sh ui.sh terminal.sh prepare.sh iso.conf version profile.sh tpot.issue tpot-iso.service tpotce.tar grub/theme.txt grub/splash.png grub/hl_c.png grub/tpot.cfg; do
  if [ ! -f "${mySRC}/${myFILE}" ];
    then
      echo "T-Pot ISO: ${mySRC}/${myFILE} is missing, the T-Pot installer is not set up." >&2
      exit 1
  fi
done

mkdir -p "${myDEST}" "${myTARGET}/usr/local/bin" "${myTARGET}/etc/profile.d" "${myTARGET}/etc/issue.d" \
  "${myTARGET}/etc/systemd/system"
for myFILE in install.sh start.sh terminal.sh prepare.sh; do
  cp "${mySRC}/${myFILE}" "${myDEST}/${myFILE}"
  chmod 0755 "${myDEST}/${myFILE}"
done
myHASH=$(sha256sum "${myDEST}/terminal.sh")
printf '%s\n' "${myHASH%% *}" > "${myDEST}/terminal.sha256"
chmod 0644 "${myDEST}/terminal.sha256"
ln -sf ../share/tpot-iso/terminal.sh "${myTARGET}/usr/local/bin/tpot-terminal"
for myFILE in ui.sh iso.conf version tpotce.tar; do
  cp "${mySRC}/${myFILE}" "${myDEST}/${myFILE}"
  chmod 0644 "${myDEST}/${myFILE}"
done
mkdir -p "${myDEST}/grub"
for myFILE in theme.txt splash.png hl_c.png tpot.cfg; do
  cp "${mySRC}/grub/${myFILE}" "${myDEST}/grub/${myFILE}"
  chmod 0644 "${myDEST}/grub/${myFILE}"
done
# gum and the wheels: without them the T-Pot installer fetches them when it starts
for myFILE in "${mySRC}"/gum_*_Linux_*.tar.gz; do
  [ -f "${myFILE}" ] || continue
  cp "${myFILE}" "${myDEST}/${myFILE##*/}"
  chmod 0644 "${myDEST}/${myFILE##*/}"
done
for myFILE in "${mySRC}"/wheels/*.whl; do
  [ -f "${myFILE}" ] || continue
  mkdir -p "${myDEST}/wheels"
  cp "${myFILE}" "${myDEST}/wheels/${myFILE##*/}"
  chmod 0644 "${myDEST}/wheels/${myFILE##*/}"
done
cp "${mySRC}/profile.sh" "${myTARGET}/etc/profile.d/tpot-iso.sh"
cp "${mySRC}/tpot.issue" "${myTARGET}/etc/issue.d/tpot.issue"
cp "${mySRC}/tpot-iso.service" "${myTARGET}/etc/systemd/system/tpot-iso.service"
chmod 0644 "${myTARGET}/etc/profile.d/tpot-iso.sh" "${myTARGET}/etc/issue.d/tpot.issue" \
  "${myTARGET}/etc/systemd/system/tpot-iso.service"
# the hint as it is now: prepare.sh --complete removes only that one, whatever T-Pot runs it later
myHASH=$(sha256sum "${myTARGET}/etc/issue.d/tpot.issue")
printf '%s\n' "${myHASH%% *}" > "${myDEST}/tpot.issue.sha256"
chmod 0644 "${myDEST}/tpot.issue.sha256"

# T-Pot, gum and the T-Pot Manager for the user of the Debian installer, and the start of the
# T-Pot installer after the boot for that user; what fails there the T-Pot installer does when it
# starts, the installation of Debian goes on
myUSER=$(debconf-get passwd/username 2>/dev/null) || myUSER=""
if in-target bash /usr/local/share/tpot-iso/prepare.sh ${myUSER:+"${myUSER}"};
  then echo "T-Pot ISO: T-Pot is ready in the home folder of ${myUSER:-the user}."
  else echo "T-Pot ISO: T-Pot could not be prepared (/var/log/tpot-iso.log), the installer gets it when it starts." >&2
fi
echo "T-Pot ISO: the T-Pot installer starts after the boot on the console (tty8), or at a login."
