#!/bin/bash

# Set TERM, DIALOGRC
export DIALOGRC=/etc/dialogrc
export TERM=linux

# Let's define some global vars
myBACKTITLE="T-Pot - ISO Creator"
# If you need latest hardware support, try using the hardware enablement (hwe) ISO
# myUBUNTULINK="http://archive.ubuntu.com/ubuntu/dists/xenial-updates/main/installer-amd64/current/images/hwe-netboot/mini.iso"
myUBUNTULINK="http://archive.ubuntu.com/ubuntu/dists/xenial-updates/main/installer-amd64/current/images/netboot/mini.iso"
myUBUNTUISO="mini.iso"
myTPOTISO="tpot.iso"
myTPOTDIR="tpotiso"
myTPOTSEED="iso/preseed/tpot.seed"
myPACKAGES="dialog genisoimage syslinux syslinux-utils pv udisks2"
myAUTHKEYSPATH="iso/installer/keys/authorized_keys"
myPFXPATH="iso/installer/keys/8021x.pfx"
myPFXPWPATH="iso/installer/keys/8021x.pw"
myPFXHOSTIDPATH="iso/installer/keys/8021x.id"
myINSTALLERPATH="iso/installer/install.sh"
myPROXYCONFIG="iso/installer/proxy"
myNTPCONFPATH="iso/installer/ntp"
myTMP="tmp"

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    sudo ./$0
    exit
fi

# Let's load dialog color theme
cp host/etc/dialogrc /etc/

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
dialog --backtitle "$myBACKTITLE" --title "[ Continue? ]" --yesno "\nDownload latest supported Ubuntu Mini ISO and build the T-Pot Install Image." 8 50
mySTART=$?
if [ "$mySTART" = "1" ];
  then
    exit
fi

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

# Let's download Ubuntu Minimal ISO
if [ ! -f $myUBUNTUISO ]
  then
    wget $myUBUNTULINK --progress=dot 2>&1 | awk '{print $7+0} fflush()' | dialog --backtitle "$myBACKTITLE" --title "[ Downloading Ubuntu ... ]" --gauge "" 5 70;
    echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Downloading Ubuntu ... Done! ]" --gauge "" 5 70;
  else
    dialog --infobox "Using previously downloaded .iso ..." 3 50;
fi

# Let's loop mount it and copy all contents
mkdir -p $myTMP $myTPOTDIR
mount -o loop $myUBUNTUISO $myTMP
rsync -a $myTMP/ $myTPOTDIR
umount $myTMP

# Let's modify initrd
gunzip $myTPOTDIR/initrd.gz
mkdir $myTPOTDIR/tmp
cd $myTPOTDIR/tmp
cpio --extract --make-directories --no-absolute-filenames < ../initrd
cd ..
rm initrd
cd ..

# Let's add the files for the automated install
mkdir -p $myTPOTDIR/tmp/opt/
cp iso/installer -R $myTPOTDIR/tmp/opt/
cp iso/isolinux/* $myTPOTDIR/
cp iso/preseed/tpot.seed $myTPOTDIR/tmp/preseed.cfg

# Let's create the new initrd
cd $myTPOTDIR/tmp
find . | cpio -H newc --create > ../initrd
cd ..
gzip initrd
rm -rf tmp
cd ..

# Let's create the new .iso
cd $myTPOTDIR
mkisofs -gui -D -r -V "T-Pot" -cache-inodes -J -l -b isolinux.bin -c boot.cat -no-emul-boot -boot-load-size 4 -boot-info-table -o ../$myTPOTISO ../$myTPOTDIR 2>&1 | awk '{print $1+0} fflush()' | cut -f1 -d"." | dialog --backtitle "$myBACKTITLE" --title "[ Building T-Pot .iso ... ]" --gauge "" 5 70 0
echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Building T-Pot .iso ... Done! ]" --gauge "" 5 70
cd ..
isohybrid $myTPOTISO
sha256sum $myTPOTISO > tpot.sha256

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
              umount $myTARGET? 2>&1 || true
              (pv -n "$myTPOTISO" | dd of="$myTARGET") 2>&1 | dialog --backtitle "$myBACKTITLE" --title "[ Writing .iso to target ... ]" --gauge "" 5 70 0
              echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Writing .iso to target ... Done! ]" --gauge "" 5 70
              udisksctl power-off -b $myTARGET 2>&1
              break
          fi
      fi
    else
      break;
  fi
done

exit 0
