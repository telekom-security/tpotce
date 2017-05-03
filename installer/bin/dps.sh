#/bin/bash
# Show current status of all running containers
# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  stty sane
}
trap fuCLEANUP EXIT

stty -echo -icanon time 0 min 0
myIMAGES=$(cat /etc/tpot/tpot.yml | grep container_name | cut -d: -f2)
while true
  do
    clear
    echo "[0;0m"
    echo "======| System |======"
    echo Date:"     "$(date)
    echo Uptime:"  "$(uptime)
    echo CPU temp: $(sensors | grep "Physical" | awk '{ print $4 }')
    echo
    printf "NAME"
    printf "%-15s STATUS"
    printf "%-13s PORTS\n"
    for i in $myIMAGES; do
      mySTATUS=$(/usr/bin/docker ps -f name=$i --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" -f status=running -f status=exited | GREP_COLORS='mt=01;35' /bin/egrep --color=always "(^[_0-9a-z-]+ |$)|$" | GREP_COLORS='mt=01;32' /bin/egrep --color=always "(Up[ 0-9a-Z ]+ |$)|$" | GREP_COLORS='mt=01;31' /bin/egrep --color=always "(Exited[ \(0-9\) ]+ [0-9a-Z ]+ ago|$)|$" | tail -n 1)
      myDOWN=$(echo "$mySTATUS" | grep -c "NAMES")
      if [ "$myDOWN" = "1" ];
        then
          printf "[1;35m%-19s [1;31mDown\n" $i
        else
          printf "$mySTATUS\n"
      fi
      if [ "$1" = "vv" ]; 
        then
          /usr/bin/docker exec -t $i /bin/ps -awfuwfxwf | egrep -v -E "awfuwfxwf|/bin/ps" 
      fi      
    done
    if [[ $1 =~ ^([1-9]|[1-9][0-9]|[1-9][0-9][0-9])$ ]];
      then 
        sleep $1
      else 
        break
    fi
done
