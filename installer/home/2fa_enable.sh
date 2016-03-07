#!/bin/bash

########################################################
# T-Pot                                                #
# Two-Factor-Authentication and SSH enable script      #
#                                                      #
# v16.03.1 by mo, DTAG, 2016-03-07                     #
########################################################
myBACKTITLE="T-Pot - Two-Factor-Authentication and SSH enable script"


# Let's ask if the user wants to enable two-factor ...
dialog --backtitle "$myBACKTITLE" --title "[ Enable 2FA? ]" --yesno "\nDo you want to enable Two-Factor-Authentication based on Google Authenticator for SSH?" 8 70
my2FA=$?

# Let's ask if the user wants to enable ssh ...
dialog --backtitle "$myBACKTITLE" --title "[ Enable SSH? ]" --yesno "\nDo you want to enable the SSH service?" 8 70
mySSH=$?

# Enable 2FA
if [ $my2FA == 0 ] && ! [ -f /etc/pam.d/sshd.bak ];
  then
    clear
    sudo sed -i.bak '\# PAM#aauth required pam_google_authenticator.so' /etc/pam.d/sshd
    sudo sed -i.bak 's#ChallengeResponseAuthentication no#ChallengeResponseAuthentication yes#' /etc/ssh/sshd_config
    google-authenticator -t -d -f -r 3 -R 30 -w 21
    echo "2FA enabled. Please press return to continue ..."
    read
  elif [ -f /etc/pam.d/sshd.bak ]
    then 
      dialog --backtitle "$myBACKTITLE" --title "[ Already enabled ]" --msgbox "\nIt seems that Two-Factor-Authentication has already been enabled. Please run 'google-authenticator -t -d -f -r 3 -R 30 -w 21' if you want to rewrite your token." 8 70  
fi

# Enable SSH
if [ $mySSH == 0 ] && [ -f /etc/init/ssh.override ];
  then
    clear
    sudo rm /etc/init/ssh.override
    sudo service ssh start
    dialog --backtitle "$myBACKTITLE" --title "[ SSH enabled ]" --msgbox "\nThe SSH service has been enabled and is now reachable via port tcp/64295. Password authentication is disabled by default." 8 70
  elif ! [ -f /etc/init/ssh.override ]
    then
      dialog --backtitle "$myBACKTITLE" --title "[ Already enabled ]" --msgbox "\nIt seems that SSH has already been enabled." 8 70
fi
