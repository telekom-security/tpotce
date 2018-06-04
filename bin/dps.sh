#/bin/bash
# Show current status of all running containers
myPARAM="$1"
myIMAGES="$(cat /opt/tpot/etc/tpot.yml | grep -v '#' | grep container_name | cut -d: -f2)"
myRED="[1;31m"
myGREEN="[1;32m"
myBLUE="[1;34m"
myWHITE="[0;0m"
myMAGENTA="[1;35m"

function fuCONTAINERSTATUS {
local myNAME="$1"
local mySTATUS="$(/usr/bin/docker ps -f name=$myNAME --format "table {{.Status}}" -f status=running -f status=exited | tail -n 1)"
myDOWN="$(echo "$mySTATUS" | grep -o -E "(STATUS|NAMES|Exited)")"

case "$myDOWN" in
  STATUS)
    mySTATUS="$myRED"DOWN"$myWHITE"
  ;;
  NAMES)
    mySTATUS="$myRED"DOWN"$myWHITE"
  ;;
  Exited)
    mySTATUS="$myRED$mySTATUS$myWHITE"
  ;;
  *)
    mySTATUS="$myGREEN$mySTATUS$myWHITE"
  ;;
esac

printf "$mySTATUS"
}

function fuCONTAINERPORTS {
local myNAME="$1"
local myPORTS="$(/usr/bin/docker ps -f name=$myNAME --format "table {{.Ports}}" -f status=running -f status=exited | tail -n 1 | sed s/","/",\n\t\t\t\t\t\t\t"/g)"

if [ "$myPORTS" != "PORTS" ];
  then
    printf "$myBLUE$myPORTS$myWHITE"
fi
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
    fuGETSYS
    printf "%-19s %-36s %s\n" "NAME" "STATUS" "PORTS"
    for i in $myIMAGES; do
          myNAME="$myMAGENTA$i$myWHITE"
          printf "%-32s %-49s %s" "$myNAME" "$(fuCONTAINERSTATUS $i)" "$(fuCONTAINERPORTS $i)"
          echo
      if [ "$myPARAM" = "vv" ]; 
        then
          /usr/bin/docker exec -t "$i" sh -c "stty rows 50 cols 1000 && /bin/ps aux | egrep -v -E 'awfuwfxwf|/bin/ps'"
      fi      
    done
    if [[ $myPARAM =~ ^([1-9]|[1-9][0-9]|[1-9][0-9][0-9])$ ]];
      then 
        sleep "$myPARAM"
      else 
        break
    fi
done
