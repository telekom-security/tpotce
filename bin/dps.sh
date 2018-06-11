#/bin/bash
# Show current status of all running containers
myPARAM="$1"

function fuGETSYS {
echo
printf "========| System |========\n"
printf "%+10s %-20s\n" "Date: " "$(date)"
printf "%+10s %-20s\n" "Uptime: " "$(uptime | cut -b 2-)"
printf "%+10s %-20s\n" "CPU temp: " "$(sensors | grep 'Physical' | awk '{ print $4" " }' | tr -d [:cntrl:])"
echo
}

while true
  do
    fuGETSYS
    grc docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    if [[ $myPARAM =~ ^([1-9]|[1-9][0-9]|[1-9][0-9][0-9])$ ]];
      then 
        sleep "$myPARAM"
      else 
        break
    fi
done
