#!/bin/sh
# Two repairs of the Debian installer, made before it loads its udebs:
# - Debian's legacy keyboard-name whitelist misses virtio keyboards. Use udev's keyboard
#   classification as well, including in the hook that copies the layout to the target.
# - The name tpot belongs to the containers of T-Pot (uid and gid 2000) and the T-Pot installer
#   refuses it as a login. user-setup refuses the names of its list of reserved names and asks for
#   another one, so tpot is put into that list.
# The optional root is for isolated tests; on the ISO this runs after mounting /cdrom.
set -e
myROOT="${1:-}"
myRESERVED="${myROOT}/usr/lib/user-setup/reserved-usernames"

fuERROR () {
  echo "T-Pot ISO: $*" >&2
  exit 1
}

# One round. The list is a file of user-setup-udeb, which the installer loads after this script: it is
# not there yet, or only half there (empty). It never fails, a name that did not get into the list is
# refused by the T-Pot installer later.
fuRESERVE_TPOT () {
  [ -s "${myRESERVED}" ] || return 0
  if grep -qx 'tpot' "${myRESERVED}"; then
    return 0
  fi
  # a list without a final newline gets one first, else tpot would join its last name
  [ -z "$(tail -c 1 "${myRESERVED}")" ] || echo >>"${myRESERVED}"
  echo tpot >>"${myRESERVED}" \
    && logger -t tpot-iso "Put the name tpot into the reserved names of user-setup."
}

# For the whole installation: a newer user-setup-udeb brings the list anew.
fuWATCH_RESERVED () {
  while :; do
    fuRESERVE_TPOT || :
    sleep 1
  done
}

# Detached from the streams of the installer. It runs this script with its output on pipes and the pipes
# of debconf on the descriptors 3 to 6 (7 to 9 are margin), and waits until every process has closed them:
# a loop that kept one would hold up the start of the installer for the whole installation. A subshell
# and not a call of the function, a redirected call keeps copies of the old streams above 9 in its
# shell. Before the keyboard: that part ends the script without a keyboard, and at an error.
( fuWATCH_RESERVED ) </dev/null >/dev/null 2>&1 3>&- 4>&- 5>&- 6>&- 7>&- 8>&- 9>&- &

myINPUT=$(udevadm info --export-db) || fuERROR "Cannot inspect the input devices."
printf '%s\n' "${myINPUT}" | grep -q '^E: ID_INPUT_KEYBOARD=1$' || exit 0

myFILES="/usr/share/console-setup/keyboard-configuration.config
/var/lib/dpkg/info/console-setup-udeb.postinst
/usr/lib/base-installer.d/20console-setup"
for myFILE in ${myFILES}; do
  [ -f "${myROOT}${myFILE}" ] \
    && [ "$(grep -c '^keyboard_present () {$' "${myROOT}${myFILE}")" = 1 ] \
    || fuERROR "Unrecognized Debian keyboard detection in ${myFILE}."
done

myCHECK='if udevadm info --export-db | grep -q "^E: ID_INPUT_KEYBOARD=1$"; then return 0; fi'
for myFILE in ${myFILES}; do
  if ! grep -Fqx "    ${myCHECK}" "${myROOT}${myFILE}"; then
    sed -i.tpot-backup "/^keyboard_present () {/a\\
    ${myCHECK}
" "${myROOT}${myFILE}"
    rm "${myROOT}${myFILE}.tpot-backup"
  fi
done

. "${myROOT}/usr/share/debconf/confmodule"
db_fget keyboard-configuration/xkb-keymap seen
if [ "${RET}" != true ]; then
  logger -t tpot-iso "Asking for the layout of the keyboard detected by udev."
  db_settitle debian-installer/console-setup-udeb/title
  # The udeb is already configured, even if its keyboard question was skipped.
  udpkg --force-configure --configure console-setup-udeb
fi
