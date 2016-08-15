#/bin/bash
while true
  do
    clear
    echo "======| System |======"
    echo Date:"     "$(date)
    echo Uptime:"  "$(uptime)
    echo CPU temp: $(sensors | grep "Physical" | awk '{ print $4 }')
    echo
    /usr/bin/docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" -f status=running -f status=exited | GREP_COLORS='mt=01;35' /bin/egrep --color=always "(^[_a-z-]+ |$)|$" | GREP_COLORS='mt=01;32' /bin/egrep --color=always "(Up[ 0-9a-Z ]+ |$)|$" | GREP_COLORS='mt=01;31' /bin/egrep --color=always "(Exited[ \(0-9\) ]+ [0-9a-Z ]+ ago|$)|$"
  if [ "$1" = "" ];
    then 
      break;
    else 
      sleep $1
  fi
done
