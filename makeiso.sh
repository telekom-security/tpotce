#!/bin/bash

########################################################
# T-Pot                                                #
# .ISO maker                                           #
#                                                      #
# v16.10.0 by mo, DTAG, 2016-05-20                     #
########################################################

# Let's define some global vars
myBACKTITLE="T-Pot - ISO Maker"
myUBUNTULINK="http://releases.ubuntu.com/16.04/ubuntu-16.04-server-amd64.iso"
myUBUNTUISO="ubuntu-16.04-server-amd64.iso"
myTPOTISO="tpot.iso"
myTPOTDIR="tpotiso"
myTPOTSEED="preseed/tpot.seed"
myPACKAGES="dialog genisoimage syslinux syslinux-utils pv"
myAUTHKEYSPATH="installer/keys/authorized_keys"
myPFXPATH="installer/keys/8021x.pfx"
myPFXPWPATH="installer/keys/8021x.pw"
myPFXHOSTIDPATH="installer/keys/8021x.id"
myINSTALLERPATH="installer/install.sh"
myPROXYCONFIG="installer/etc/proxy"
myNTPCONFPATH="installer/etc/ntp"
myTMP="tmp"

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Please run as root ..."
    exit
fi

# Let's clean up at the end or if something goes wrong ...
function fuCLEANUP {
rm -rf $myTMP $myTPOTDIR $myPROXYCONFIG $myPFXPATH $myPFXPWPATH $myPFXHOSTIDPATH $myNTPCONFPATH
echo > $myAUTHKEYSPATH
if [ -f $myTPOTSEED.bak ];
  then
    mv $myTPOTSEED.bak $myTPOTSEED
fi
}
trap fuCLEANUP EXIT

# Let's create a function for validating an IPv4 address
function valid_ip()
{
    local  ip=$1
    local  stat=1

    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        OIFS=$IFS
        IFS='.'
        ip=($ip)
        IFS=$OIFS
        [[ ${ip[0]} -le 255 && ${ip[1]} -le 255 \
            && ${ip[2]} -le 255 && ${ip[3]} -le 255 ]]
        stat=$?
    fi
    return $stat
}

# Let's check if all dependencies are met
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

# Let's ask if the user wants to run the script ...
dialog --backtitle "$myBACKTITLE" --title "[ Continue? ]" --yesno "\nThis script will download the latest supported Ubuntu Server and build the T-Pot .iso" 8 50
mySTART=$?
if [ "$mySTART" = "1" ];
  then
    exit
fi

# Let's ask for the type of installation SENSOR, INDUSTRIAL or FULL?
myFLAVOR=$(dialog --no-cancel --backtitle "$myBACKTITLE" --title "[ Installation type ... ]" --radiolist "" 11 76 4 "TPOT" "Standard (w/o INDUSTRIAL)" on "HP" "Honeypots only (w/o INDUSTRIAL)" off "INDUSTRIAL" "ConPot, eMobility, ELK, Suricata (8GB RAM recommended)" off "ALL" "Everything (8GB RAM required)" off 3>&1 1>&2 2>&3 3>&-)
sed -i 's#^myFLAVOR=.*#myFLAVOR="'$myFLAVOR'"#' $myINSTALLERPATH

# Let's ask the user for a proxy ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ Proxy Settings ]" --yesno "\nDo you want to configure a proxy?" 7 50
  myADDPROXY=$?
  if [ "$myADDPROXY" = "0" ]
    then
      myIPRESULT="false"
      while [ "$myIPRESULT" = "false" ];
        do
          myPROXYIP=$(dialog --backtitle "$myBACKTITLE" --no-cancel --title "Proxy IP?" --inputbox "" 7 50 "1.2.3.4" 3>&1 1>&2 2>&3 3>&-)
          if valid_ip $myPROXYIP; then myIPRESULT="true"; fi
      done
      myPORTRESULT="false"
      while [ "$myPORTRESULT" = "false" ];
        do
          myPROXYPORT=$(dialog --backtitle "$myBACKTITLE" --no-cancel --title "Proxy Port (i.e. 3128)?" --inputbox "" 7 50 "3128" 3>&1 1>&2 2>&3 3>&-)
          if [[ $myPROXYPORT =~ ^-?[0-9]+$ ]] && [ $myPROXYPORT -gt 0 ] && [ $myPROXYPORT -lt 65536 ]; then myPORTRESULT="true"; fi
      done
      echo http://$myPROXYIP:$myPROXYPORT > $myPROXYCONFIG
      sed -i.bak 's#d-i mirror/http/proxy.*#d-i mirror/http/proxy string http://'$myPROXYIP':'$myPROXYPORT'/#' $myTPOTSEED
      break
    else
      break
  fi
done

# Let's ask the user for ssh keys ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ Add ssh keys? ]" --yesno "\nDo you want to add public key(s) to authorized_keys file?" 8 50
  myADDKEYS=$?
  if [ "$myADDKEYS" = "0" ]
    then
      myKEYS=$(dialog --backtitle "$myBACKTITLE" --fselect "/" 15 50 3>&1 1>&2 2>&3 3>&-)
      if [ -f "$myKEYS" ]
        then
          cat $myKEYS > $myAUTHKEYSPATH
          break
        else
          dialog --backtitle "$myBACKTITLE" --title "[ Try again! ]" --msgbox "\nThis is no regular file." 7 50;
      fi
    else
      echo > $myAUTHKEYSPATH
      break
  fi
done

# Let's ask the user for 802.1x data ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ Need 802.1x auth? ]" --yesno "\nDo you want to add a 802.1x host certificate?" 7 50
  myADDPFX=$?
  if [ "$myADDPFX" = "0" ]
    then
      myPFX=$(dialog --backtitle "$myBACKTITLE" --fselect "/" 15 50 3>&1 1>&2 2>&3 3>&-)
      if [ -f "$myPFX" ]
        then
          cp $myPFX $myPFXPATH
          dialog --backtitle "$myBACKTITLE" --title "[ Password protected? ]" --yesno "\nDoes the certificate need your password?" 7 50
          myADDPFXPW=$?
          if [ "$myADDPFXPW" = "0" ]
            then
              myPFXPW=$(dialog --backtitle "$myBACKTITLE" --no-cancel --inputbox "Password?" 7 50 3>&1 1>&2 2>&3 3>&-)
              echo $myPFXPW > $myPFXPWPATH
          fi
          myPFXHOSTID=$(dialog --backtitle "$myBACKTITLE" --no-cancel --inputbox "Host ID?" 7 50 "<HOSTNAME>.<DOMAIN>" 3>&1 1>&2 2>&3 3>&-)
          echo $myPFXHOSTID > $myPFXHOSTIDPATH
          break
        else
          dialog --backtitle "$myBACKTITLE" --title "[ Try again! ]" --msgbox "\nThis is no regular file." 7 50;
      fi
    else
      break
  fi
done

# Let's ask the user for a ntp server ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ NTP server? ]" --yesno "\nDo you want to configure a ntp server?" 7 50
  myADDNTP=$?
  if [ "$myADDNTP" = "0" ]
    then
      myIPRESULT="false"
      while [ "$myIPRESULT" = "false" ];
        do
          myNTPIP=$(dialog --backtitle "$myBACKTITLE" --no-cancel --title "NTP IP?" --inputbox "" 7 50 "1.2.3.4" 3>&1 1>&2 2>&3 3>&-)
          if valid_ip $myNTPIP; then myIPRESULT="true"; fi
      done
tee $myNTPCONFPATH <<EOF
driftfile /var/lib/ntp/ntp.drift

statistics loopstats peerstats clockstats
filegen loopstats file loopstats type day enable
filegen peerstats file peerstats type day enable
filegen clockstats file clockstats type day enable

server $myNTPIP

restrict -4 default kod notrap nomodify nopeer noquery
restrict -6 default kod notrap nomodify nopeer noquery
restrict 127.0.0.1
restrict ::1
EOF

      break
    else
      break
  fi
done

# Let's get Ubuntu 14.04.4 as .iso
if [ ! -f $myUBUNTUISO ]
  then
    wget $myUBUNTULINK --progress=dot 2>&1 | awk '{print $7+0} fflush()' | dialog --backtitle "$myBACKTITLE" --title "[ Downloading Ubuntu ... ]" --gauge "" 5 70;
    echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Downloading Ubuntu ... Done! ]" --gauge "" 5 70;
  else
    dialog --infobox "Using previously downloaded .iso ..." 3 50;
fi

# Let's loop mount it and copy all contents
mkdir -p $myTMP $myTPOTDIR
losetup /dev/loop0 $myUBUNTUISO
mount /dev/loop0 $myTMP
cp -rT $myTMP $myTPOTDIR
chmod 777 -R $myTPOTDIR
umount $myTMP
losetup -d /dev/loop0

# Let's add the files for the automated install
mkdir -p $myTPOTDIR/tpot
cp installer/* -R $myTPOTDIR/tpot/
cp isolinux/* $myTPOTDIR/isolinux/
cp kickstart/* $myTPOTDIR/tpot/
cp preseed/* $myTPOTDIR/tpot/
if [ -d images ];
  then
    cp -R images $myTPOTDIR/tpot/images/
fi
chmod 777 -R $myTPOTDIR

# Let's create the new .iso
cd $myTPOTDIR
mkisofs -gui -D -r -V "T-Pot" -cache-inodes -J -l -b isolinux/isolinux.bin -c isolinux/boot.cat -no-emul-boot -boot-load-size 4 -boot-info-table -o ../$myTPOTISO ../$myTPOTDIR 2>&1 | awk '{print $1+0} fflush()' | cut -f1 -d"." | dialog --backtitle "$myBACKTITLE" --title "[ Building T-Pot .iso ... ]" --gauge "" 5 70 0
echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Building T-Pot .iso ... Done! ]" --gauge "" 5 70
cd ..
isohybrid $myTPOTISO

# Let's write the image
while true;
do
  dialog --backtitle "$myBACKTITLE" --yesno "\nWrite .iso to USB drive?" 7 50
  myUSBCOPY=$?
  if [ "$myUSBCOPY" = "0" ]
    then
      myTARGET=$(dialog --backtitle "$myBACKTITLE" --title "[ Select target device ... ]" --menu "" 16 40 10 $(lsblk -io NAME,SIZE -dnp) 3>&1 1>&2 2>&3 3>&-)
      if [ "$myTARGET" != "" ]
        then
          dialog --backtitle "$myBACKTITLE" --yesno "\nWrite .iso to "$myTARGET"?" 7 50
          myWRITE=$?
          if [ "$myWRITE" = "0" ]
            then
              umount $myTARGET 2>&1 || true
              (pv -n "$myTPOTISO" | dd of="$myTARGET") 2>&1 | dialog --backtitle "$myBACKTITLE" --title "[ Writing .iso to target ... ]" --gauge "" 5 70 0
              echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Writing .iso to target ... Done! ]" --gauge "" 5 70
              break
          fi
      fi
    else
      break;
  fi
done

exit 0
