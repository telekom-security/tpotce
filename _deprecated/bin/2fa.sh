#!/bin/bash

# Make sure script is started as non-root.
myWHOAMI=$(whoami)
if [ "$myWHOAMI" = "root" ]
  then
    echo "Need to run as non-root ..."
    echo ""
    exit
fi

# set vars, check deps
myPAM_COCKPIT_FILE="/etc/pam.d/cockpit"
if ! [ -s "$myPAM_COCKPIT_FILE" ];
  then
    echo "### Cockpit PAM module config does not exist. Something went wrong."
    echo ""
    exit 1
fi
myPAM_COCKPIT_GA="

# google authenticator for two-factor
auth required pam_google_authenticator.so
"
myAUTHENTICATOR=$(which google-authenticator)
if [ "$myAUTHENTICATOR" == "" ];
  then
    echo "### Could not locate google-authenticator, trying to install (if asked provide root password)."
    echo ""
    sudo apt-get update
    sudo apt-get install -y libpam-google-authenticator
    exec "$1" "$2"    
    exit 1
fi


# write PAM changes 
function fuWRITE_PAM_CHANGES {
  myCHECK=$(cat $myPAM_COCKPIT_FILE | grep -c "google")
  if ! [ "$myCHECK" == "0" ];
    then
      echo "### PAM config already enabled. Skipped."
      echo ""
    else
      echo "### Updating PAM config for Cockpit (if asked provide root password)."
      echo "$myPAM_COCKPIT_GA" | sudo tee -a $myPAM_COCKPIT_FILE
      sudo systemctl restart cockpit
  fi
}

# create 2fa
function fuGEN_TOKEN {
  echo "### Now generating token for Google Authenticator."
  echo ""
  google-authenticator -t -d -r 3 -R 30 -w 17
}


# main
echo "### This script will enable Two Factor Authentication for Cockpit."
echo ""
echo "### Please download one of the many authenticator apps from the appstore of your choice."
echo ""
while true;
  do
    read -p "### Ready to start (y/n)? " myANSWER
    case $myANSWER in
      [Yy]* ) echo "### OK. Starting ..."; break;;
      [Nn]* ) echo "### Exiting."; exit;;
    esac
done

fuWRITE_PAM_CHANGES
fuGEN_TOKEN

echo "Done. Re-run this script by every user who needs Cockpit access."
echo ""
