# shellcheck shell=sh
# The T-Pot ISO (/etc/profile.d/tpot-iso.sh, put here by late.sh): starts the T-Pot installer of the
# ISO at an interactive login, on the console or via SSH, until T-Pot is installed (tpot.service).
# Never for root, never without a terminal, not again inside the installer. Abort in the installer
# keeps a plain Debian and asks again at the next login (and after the next boot, tpot-iso.service).
#   bash /usr/local/share/tpot-iso/start.sh   starts it by hand
#   TPOT_ISO=off                              skips it for one login
#   sudo rm /etc/profile.d/tpot-iso.sh        stops asking for good
# Sourced by the login shell: no variables of its own, no exit.
if [ "${TPOT_ISO:-}" != "off" ] && [ -z "${TPOT_ISO_STARTED:-}" ] \
  && [ -r /usr/local/share/tpot-iso/iso.conf ] && [ ! -e /etc/systemd/system/tpot.service ] \
  && [ -t 0 ] && [ -t 1 ] && [ "$(id -u)" != "0" ];
  then
    case $- in
      *i*)
        if id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx sudo;
          then
            TPOT_ISO_STARTED=1 bash /usr/local/share/tpot-iso/start.sh
          else
            echo "T-Pot ISO: $(id -un) may not use sudo. Log in as the user created during the installation to install T-Pot."
        fi
        ;;
    esac
fi
