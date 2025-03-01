#!/bin/bash

myHOST="$1"
myPACKAGES="dcmtk ncat nmap yq"
myDOCKERCOMPOSEYML="$HOME/tpotce/docker-compose.yml"
myTIMEOUT=180
myMEDPOTPACKET="
MSH|^~\&|ADT1|MCM|LABADT|MCM|198808181126|SECURITY|ADT^A01|MSG00001-|P|2.6
EVN|A01|198808181123
PID|||PATID1234^5^M11^^AN||JONES^WILLIAM^A^III||19610615|M||2106-3|677 DELAWARE AVENUE^^EVERETT^MA^02149|GL|(919)379-1212|(919)271-3434~(919)277-3114||S||PATID12345001^2^M10^^ACSN|123456789|9-87654^NC
NK1|1|JONES^BARBARA^K|SPO|||||20011105
NK1|1|JONES^MICHAEL^A|FTH
PV1|1|I|2000^2012^01||||004777^LEBAUER^SIDNEY^J.|||SUR||-||ADM|A0
AL1|1||^PENICILLIN||CODE16~CODE17~CODE18
AL1|2||^CAT DANDER||CODE257
DG1|001|I9|1550|MAL NEO LIVER, PRIMARY|19880501103005|F
PR1|2234|M11|111^CODE151|COMMON PROCEDURES|198809081123
ROL|45^RECORDER^ROLE MASTER LIST|AD|RO|KATE^SMITH^ELLEN|199505011201
GT1|1122|1519|BILL^GATES^A
IN1|001|A357|1234|BCMD|||||132987
IN2|ID1551001|SSN12345678
ROL|45^RECORDER^ROLE MASTER LIST|AD|RO|KATE^ELLEN|199505011201"

function fuCHECKDEPS {
myINST=""
for myDEPS in $myPACKAGES;
do
  myOK=$(sudo dpkg -s $myDEPS | grep ok | awk '{ print $3 }');
  if [ "$myOK" != "ok" ]
    then
      myINST=$(echo $myINST $myDEPS)
  fi
done
if [ "$myINST" != "" ]
  then
    sudo apt-get update -y
    for myDEPS in $myINST;
    do
      sudo apt-get install $myDEPS -y
    done
fi
}

function fuCHECKFORARGS {
if [ "$myHOST" != "" ];
  then
    echo "All arguments met. Continuing."
    echo
  else
    echo "Usage: hptest.sh <[host or ip]>"
    echo
    exit
fi
}

function fuGETPORTS {
myDOCKERCOMPOSEUDPPORTS=$(cat $myDOCKERCOMPOSEYML | grep "udp" | tr -d '"\|#\-' | cut -d ":" -f2 | cut -d "/" -f1 | sort -gu)
myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ' | sed -e s/^:// | cut -f1 -d ':' | grep -v "6429\|6430" | sort -gu)
myUDPPORTS=$(for i in $myDOCKERCOMPOSEUDPPORTS; do echo -n "U:$i,"; done)
myPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo -n "T:$i,"; done)
#echo ${myUDPPORTS}
#echo ${myPORTS}
}

# Main
fuCHECKFORARGS
fuCHECKDEPS
fuGETPORTS
echo
echo "Probing some services ..."
echo "$myMEDPOTPACKET" | nc "$myHOST" 2575 &
curl -XGET "http://$myHOST:9200/logstash-*/_search" &
curl -XPOST -H "Content-Type: application/json" -d '{"name":"test","email":"test@test.com"}' "http://$myHOST:9200/test" &
echo "I20100" | timeout --foreground 3 nc "$myHOST" 10001 &
findscu -P -k PatientName="*" $myHOST 11112 &
getscu -P -k PatientName="*" $myHOST 11112 &
telnet $myHOST 3299 &
echo
echo "Starting scan on all UDP / TCP ports defined in ${myDOCKERCOMPOSEYML} ..."
timeout --foreground ${myTIMEOUT} nmap -sV -sC -v -p $myPORTS $1 &
timeout --foreground ${myTIMEOUT} nmap -sU -sV -sC -v -p $myUDPPORTS $1 &
echo
wait
echo "Restarting some containers ..."
docker stop adbhoney conpot_guardian_ast conpot_kamstrup_382 dionaea
docker start adbhoney conpot_guardian_ast conpot_kamstrup_382 dionaea
echo
echo "Resetting terminal ..."
reset
echo
echo "Done."
echo
