#!/bin/sh
# Debian's legacy keyboard-name whitelist misses virtio keyboards. Use udev's keyboard
# classification as well, including in the hook that copies the layout to the target.
# The optional root is for isolated tests; on the ISO this runs after mounting /cdrom.
set -e
myROOT="${1:-}"

fuERROR () {
  echo "T-Pot ISO: $*" >&2
  exit 1
}

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
