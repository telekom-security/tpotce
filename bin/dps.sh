#/bin/bash
# Show current status of T-Pot containers
myPARAM="$1"
myCONTAINERS="$(cat /opt/tpot/etc/tpot.yml | grep -v '#' | grep container_name | cut -d: -f2 | sort | tr -d " ")"
myRED="[1;31m"
myGREEN="[1;32m"
myBLUE="[1;34m"
myWHITE="[0;0m"
myMAGENTA="[1;35m"

function fuGETSTATUS {
grc docker ps -f status=running -f status=exited --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -v "NAME" | sort
}

function fuGETSYS {
printf "========| System |========\n"
printf "%+10s %-20s\n" "Date: " "$(date)"
printf "%+10s %-20s\n" "Uptime: " "$(uptime | cut -b 2-)"
printf "%+10s %-20s\n" "CPU temp: " "$(sensors | grep 'Physical' | awk '{ print $4" " }' | tr -d [:cntrl:])"
echo
}

while true
  do
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
    if [[ $myPARAM =~ ^([1-9]|[1-9][0-9]|[1-9][0-9][0-9])$ ]];
      then 
        sleep "$myPARAM"
      else 
        break
    fi
done
