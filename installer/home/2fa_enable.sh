#!/bin/bash

########################################################
# T-Pot                                                #
# Two-Factor authentication enable script              #
#                                                      #
# v0.01 by mo, DTAG, 2015-06-15                        #
########################################################

echo "### This script will enable Two-Factor-Authentication based on Google Authenticator for SSH."
while true
do
  echo -n "### Do you want to continue (y/n)? "; read myANSWER;
  case $myANSWER in
    n)
      echo "### Exiting."
      exit 0;
      ;;
    y)
      break
      ;;
  esac
done
if [ -f /etc/pam.d/sshd.bak ];
  then echo "### Already enabled. Exiting."
  exit 1;
fi
sudo sed -i.bak '\# PAM#aauth required pam_google_authenticator.so' /etc/pam.d/sshd
sudo sed -i.bak 's#ChallengeResponseAuthentication no#ChallengeResponseAuthentication yes#' /etc/ssh/sshd_config
google-authenticator -t -d -f -r 3 -R 30 -w 21
echo "### Please do not forget to run the ssh_enable script."
