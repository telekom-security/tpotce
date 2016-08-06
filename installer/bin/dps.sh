#/bin/bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" -f status=running -f status=exited | GREP_COLORS='mt=01;35' egrep --color=always "(^[_a-z]+ |$)|$" | GREP_COLORS='mt=01;32' egrep --color=always "(Up[ 0-9a-Z]+ |$)|$" | GREP_COLORS='mt=01;31' egrep --color=always "(Exited[ \(0-9\)]+ [0-9a-Z ]+ ago|$)|$"
