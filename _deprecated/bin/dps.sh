#!/bin/bash

# Run as root only.
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

myPARAM="$1"
if [[ $myPARAM =~ ^([1-9]|[1-9][0-9]|[1-9][0-9][0-9])$ ]];
  then
    watch --color -n $myPARAM "$0"
    exit
fi

# Show current status of T-Pot containers
myCONTAINERS="$(cat /opt/tpot/etc/tpot.yml | grep -v '#' | grep container_name | cut -d: -f2 | sort | tr -d " ")"
myRED="[1;31m"
myGREEN="[1;32m"
myBLUE="[1;34m"
myWHITE="[0;0m"
myMAGENTA="[1;35m"

# Blackhole Status
myBLACKHOLE_STATUS=$(ip r | grep "blackhole" -c)
if [ "$myBLACKHOLE_STATUS" -gt "500" ];
  then
    myBLACKHOLE_STATUS="${myGREEN}ENABLED"
  else
    myBLACKHOLE_STATUS="${myRED}DISABLED"
fi

function fuGETTPOT_STATUS {
# T-Pot Status
myTPOT_STATUS=$(systemctl status tpot | grep "Active" | awk '{ print $2 }')
if [ "$myTPOT_STATUS" == "active" ];
  then
    echo "${myGREEN}ACTIVE"
  else
    echo "${myRED}INACTIVE"
fi
}

function fuGETSTATUS {
grc --colour=on docker ps -f status=running -f status=exited --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -v "NAME" | sort
}

function fuGETSYS {
printf "[ ========| System |======== ]\n"
printf "${myBLUE}%+11s ${myWHITE}%-20s\n" "DATE: " "$(date)"
printf "${myBLUE}%+11s ${myWHITE}%-20s\n" "UPTIME: " "$(grc --colour=on uptime)"
printf "${myMAGENTA}%+11s %-20s\n" "T-POT: " "$(fuGETTPOT_STATUS)"
printf "${myMAGENTA}%+11s %-20s\n" "BLACKHOLE: " "$myBLACKHOLE_STATUS${myWHITE}"
echo
}

    myDPS=$(fuGETSTATUS)
    myDPSNAMES=$(echo "$myDPS" | awk '{ print $1 }' | sort)
    fuGETSYS
    printf "%-21s %-28s %s\n" "NAME" "STATUS" "PORTS"
    if [ "$myDPS" != "" ];
      then
        echo "$myDPS"
    fi
    for i in $myCONTAINERS; do
      myAVAIL=$(echo "$myDPSNAMES" | grep -o "$i" | uniq | wc -l)      	    
      if [ "$myAVAIL" = "0" ];
	then
	  printf "%-28s %-28s\n" "$myRED$i" "DOWN$myWHITE"
      fi
    done
