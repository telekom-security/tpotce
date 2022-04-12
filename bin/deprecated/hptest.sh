#!/bin/bash

myHOST="$1"
myPACKAGES="dcmtk netcat nmap"
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

function fuGOTROOT {
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi
}

function fuCHECKDEPS {
myINST=""
for myDEPS in $myPACKAGES;
do
  myOK=$(dpkg -s $myDEPS | grep ok | awk '{ print $3 }');
  if [ "$myOK" != "ok" ]
    then
      myINST=$(echo $myINST $myDEPS)
  fi
done
if [ "$myINST" != "" ]
  then
    apt-get update -y
    for myDEPS in $myINST;
    do
      apt-get install $myDEPS -y
    done
fi
}

function fuCHECKFORARGS {
if [ "$myHOST" != "" ];
  then
    echo "All arguments met. Continuing."
  else
    echo "Usage: hp_test.sh <[host or ip]>"
    exit
fi
}

function fuGETPORTS {
myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ' | sed -e s/^:// | cut -f1 -d ':' | grep -v "6429\|6430" | sort -gu)
myPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo "$i"; done)
echo "Found these ports enabled:"
echo "$myPORTS"
exit
}

function fuSCAN {
local myTIMEOUT="$1"
local mySCANPORT="$2"
local mySCANIP="$3"
local mySCANOPTS="$4"

timeout --foreground ${myTIMEOUT} nmap ${mySCANOPTS} -T4 -v -p ${mySCANPORT} ${mySCANIP} &
}

# Main
fuGOTROOT
fuCHECKDEPS
fuCHECKFORARGS

echo "Starting scans ..."
echo "$myMEDPOTPACKET" | nc "$myHOST" 2575 &
curl -XGET "http://$myHOST:9200/logstash-*/_search" &
curl -XPOST -H "Content-Type: application/json" -d '{"name":"test","email":"test@test.com"}' "http://$myHOST:9200/test" &
echo "I20100" | timeout --foreground 3 nc "$myHOST" 10001 &
findscu -P -k PatientName="*" $myHOST 11112 &
getscu -P -k PatientName="*" $myHOST 11112 &
telnet $myHOST 3299 &
fuSCAN "180" "7,8,102,135,161,1025,1080,5000,9200" "$myHOST" "-sC -sS -sU -sV"
fuSCAN "180" "2048,4096,5432" "$myHOST" "-sC -sS -sU -sV --version-light"
fuSCAN "120" "20,21" "$myHOST" "--script=ftp* -sC -sS -sV"
fuSCAN "120" "22" "$myHOST" "--script=ssh2-enum-algos,ssh-auth-methods,ssh-hostkey,ssh-publickey-acceptance,sshv1 -sC -sS -sV"
fuSCAN "30" "22" "$myHOST" "--script=ssh-brute"
fuSCAN "120" "23,2323,2324" "$myHOST" "--script=telnet-encryption,telnet-ntlm-info -sC -sS -sV --version-light"
fuSCAN "120" "25" "$myHOST" "--script=smtp* -sC -sS -sV"
fuSCAN "180" "42" "$myHOST" "-sC -sS -sV"
fuSCAN "120" "69" "$myHOST" "--script=tftp-enum -sU"
fuSCAN "120" "80,81,8080,8443" "$myHOST" "-sC -sS -sV"
fuSCAN "120" "110,995" "$myHOST" "--script=pop3-capabilities,pop3-ntlm-info -sC -sS -sV --version-light"
fuSCAN "30" "110,995" "$myHOST" "--script=pop3-brute -sS"
fuSCAN "120" "143,993" "$myHOST" "--script=imap-capabilities,imap-ntlm-info -sC -sS -sV --version-light"
fuSCAN "30" "143,993" "$myHOST" "--script=imap-brute -sS"
fuSCAN "240" "445" "$myHOST" "--script=smb-vuln* -sS -sU"
fuSCAN "120" "502" "$myHOST" "--script=modbus-discover -sS -sU"
fuSCAN "120" "623" "$myHOST" "--script=ipmi-cipher-zero,ipmi-version,supermicro-ipmi -sS -sU"
fuSCAN "30" "623" "$myHOST" "--script=ipmi-brute -sS -sU"
fuSCAN "120" "1433" "$myHOST" "--script=ms-sql* -sS"
fuSCAN "120" "1723" "$myHOST" "--script=pptp-version -sS"
fuSCAN "120" "1883" "$myHOST" "--script=mqtt-subscribe -sS"
fuSCAN "120" "2404" "$myHOST" "--script=iec-identify -sS"
fuSCAN "120" "3306" "$myHOST" "--script=mysql-vuln* -sC -sS -sV"
fuSCAN "120" "3389" "$myHOST" "--script=rdp* -sC -sS -sV"
fuSCAN "120" "5000" "$myHOST" "--script=*upnp* -sS -sU"
fuSCAN "120" "5060,5061" "$myHOST" "--script=sip-call-spoof,sip-enum-users,sip-methods -sS -sU"
fuSCAN "120" "5900" "$myHOST" "--script=vnc-info,vnc-title,realvnc-auth-bypass -sS"
fuSCAN "120" "27017" "$myHOST" "--script=mongo* -sS"
fuSCAN "120" "47808" "$myHOST" "--script=bacnet* -sS"
wait
reset
echo "Done."
