#!/bin/bash
#############################################################
# T-Pot Community Edition - disable splash boot permanently #
# Ubuntu server 14.04, x64                                  #
#                                                           #
# v0.04 by mo, 2014-11-28                                   #
#############################################################

# Let's comment out the "quiet splash" options and update grub
sed -i.bak 's#GRUB_CMDLINE_LINUX_DEFAULT#\#GRUB_CMDLINE_LINUX_DEFAULT#' /etc/default/grub
update-grub

# Let's move the install script to rc.local and reboot
mv /root/install.sh /etc/rc.local && sleep 2 && reboot
