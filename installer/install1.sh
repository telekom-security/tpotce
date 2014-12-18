#!/bin/bash
#############################################################
# T-Pot Community Edition - disable splash boot             #
#                           and consoleblank permanently    #
# Ubuntu server 14.04.1, x64                                #
#                                                           #
# v0.05 by mo, DTAG, 2014-12-18                             #
#############################################################

# Let's replace "quiet splash" options and update grub
sed -i.bak 's#GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"#GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0"#' /etc/default/grub
update-grub

# Let's move the install script to rc.local and reboot
mv /root/install.sh /etc/rc.local && sleep 2 && reboot
