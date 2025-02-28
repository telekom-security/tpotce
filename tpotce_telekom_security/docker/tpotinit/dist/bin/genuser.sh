#!/usr/bin/env bash
myTPOT_CONF_FILE=/data/.env

# Read WEB_USER from file
WEB_USER=$(grep "^WEB_USER=" "${myTPOT_CONF_FILE}" | sed 's/^WEB_USER=//g' | tr -d "\"'")

myPW=$(cat << "EOF"
__        __   _     _   _  [ T-Pot ]
\ \      / /__| |__ | | | |___  ___ _ __
 \ \ /\ / / _ \ '_ \| | | / __|/ _ \ '__|
  \ V  V /  __/ |_) | |_| \__ \  __/ |
   \_/\_/ \___|_.__/ \___/|___/\___|_|
EOF
)

# Generate T-Pot WebUser
echo "$myPW"
echo
echo "### This script will ask for and create T-Pot web users."
echo

# Preparing web user for T-Pot
echo
echo "### T-Pot User Configuration ..."
echo
# Asking for web user name
myWEB_USER=""
while [ 1 != 2 ];
  do
    myOK=""
    read -rp "### Enter your web user name: " myWEB_USER
    myWEB_USER=$(echo $myWEB_USER | tr -cd "[:alnum:]_.-")
    echo "### Your username is: ${myWEB_USER}"
    while [[ ! "${myOK}" =~ [YyNn] ]];
      do
        read -rp "### Is this correct? (y/n) " myOK
      done
    if [[ "${myOK}" =~ [Yy] ]] && [ "$myWEB_USER" != "" ];
      then
        break
      else
        echo
    fi
  done

# Asking for web user password
myWEB_PW="pass1"
myWEB_PW2="pass2"
mySECURE=0
myOK=""
while [ "${myWEB_PW}" != "${myWEB_PW2}"  ] && [ "${mySECURE}" == "0" ]
  do
    echo
    while [ "${myWEB_PW}" == "pass1"  ] || [ "${myWEB_PW}" == "" ]
      do
        read -rsp "### Enter password for your web user: " myWEB_PW
        echo
      done
    read -rsp "### Repeat password you your web user: " myWEB_PW2
    echo
    if [ "${myWEB_PW}" != "${myWEB_PW2}" ];
      then
        echo "### Passwords do not match."
        myWEB_PW="pass1"
        myWEB_PW2="pass2"
    fi
	mySECURE=$(printf "%s" "$myWEB_PW" | /usr/sbin/cracklib-check | grep -c "OK")
    if [ "$mySECURE" == "0" ] && [ "$myWEB_PW" == "$myWEB_PW2" ];
      then
        while [[ ! "${myOK}" =~ [YyNn] ]];
          do
            read -rp "### Keep insecure password? (y/n) " myOK
          done
        if [[ "${myOK}" =~ [Nn] ]] || [ "$myWEB_PW" == "" ];
          then
            myWEB_PW="pass1"
            myWEB_PW2="pass2"
            mySECURE=0
            myOK=""
        fi
    fi
done

# Write username and password to T-Pot config file
echo "### Creating base64 encoded htpasswd username and password for T-Pot config file: ${myTPOT_CONF_FILE}"
myWEB_USER_ENC=$(htpasswd -b -n "${myWEB_USER}" "${myWEB_PW}")
myWEB_USER_ENC_B64=$(echo -n "${myWEB_USER_ENC}" | base64 -w0)

# Add the new web user
if [ "${WEB_USER}" == "" ];
  then
    WEB_USER="${myWEB_USER_ENC_B64}"
  else
    WEB_USER="${WEB_USER} ${myWEB_USER_ENC_B64}"
fi
sed -i "s|^WEB_USER=.*|WEB_USER=${WEB_USER}|" ${myTPOT_CONF_FILE}

# Done
echo
echo "### The following users are now configured in the .env:"
echo
for i in ${WEB_USER};
  do
    if [[ -n $i ]]; 
      then
        # Need to control newlines as they kept coming up for some reason
        echo -n "$i" | base64 -d -w0 | tr -d '\n'; echo -n " => [$i]"; 
        echo
    fi
  done
echo
echo "### You can remove them by opening the .env and adjust the WEB_USER entry."
echo
echo "### Done."
echo
