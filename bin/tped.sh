#!/bin/bash

# set backtitle, get filename
myBACKTITLE="T-Pot Edition Selection Tool"
myYMLS=$(cd /opt/tpot/etc/compose/ && ls -1 *.yml)
myLINK="/opt/tpot/etc/tpot.yml"

# setup menu
for i in $myYMLS;
  do
    myITEMS+="$i $(echo $i | cut -d "." -f1 | tr [:lower:] [:upper:]) " 
done
myEDITION=$(dialog --backtitle "$myBACKTITLE" --menu "Select T-Pot Edition" 13 50 6 $myITEMS 3>&1 1>&2 2>&3 3>&-)
if [ "$myEDITION" == "" ];
  then
    echo "Have a nice day!"
    exit
fi
dialog --backtitle "$myBACKTITLE" --title "[ Activate now? ]" --yesno "\n$myEDITION" 7 50
myOK=$?
if [ "$myOK" == "0" ];
  then
    echo "OK - Activating"
    systemctl stop tpot
    rm -f $myLINK
    ln -s /opt/tpot/etc/compose/$myEDITION $myLINK
    systemctl start tpot
    echo "Done. Use \"dps.sh\" for monitoring"
  else 
    echo "Have a nice day!"
fi
