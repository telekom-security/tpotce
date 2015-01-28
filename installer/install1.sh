#!/bin/bash
#############################################################
# T-Pot Community Edition - disable splash boot             #
#                           and consoleblank permanently    #
# Ubuntu server 14.04.1, x64                                #
#                                                           #
# v0.11 by mo, DTAG, 2015-01-28                             #
#############################################################

# Let's replace "quiet splash" options and update grub
sed -i 's#GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"#GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0"#' /etc/default/grub
sed -i 's#\#GRUB_GFXMODE=640x480#GRUB_GFXMODE=800x600#' /etc/default/grub
update-grub
sed -i 's#FONTFACE="VGA"#FONTFACE="Terminus"#' /etc/default/console-setup
sed -i 's#FONTSIZE="16"#FONTSIZE="12x6"#' /etc/default/console-setup

# Let's move the install script to rc.local and reboot
mv /root/tpotce/install2.sh /etc/rc.local && sleep 2 && reboot
