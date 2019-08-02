#!/bin/bash

# Set TERM, DIALOGRC
export TERM=linux

# Let's define some global vars
myBACKTITLE="T-Pot - ISO Creator"
#myMINIISOLINK="http://ftp.debian.org/debian/dists/testing/main/installer-amd64/current/images/netboot/mini.iso"
#myMINIISOLINK="https://d-i.debian.org/daily-images/amd64/daily/netboot/mini.iso"
# For stability reasons Debian Sid installation is built on a stable installer
myMINIISOLINK="http://ftp.debian.org/debian/dists/buster/main/installer-amd64/current/images/netboot/mini.iso"
myMINIISO="mini.iso"
myTPOTISO="tpot.iso"
myTPOTDIR="tpotiso"
myTPOTSEED="iso/preseed/tpot.seed"
myPACKAGES="dialog genisoimage syslinux syslinux-utils pv rsync udisks2 xorriso"
myPFXFILE="iso/installer/keys/8021x.pfx"
myINSTALLERPATH="iso/installer/install.sh"
myNTPCONFFILE="iso/installer/ntp.conf"
myTMP="tmp"
myCONF_FILE="iso/installer/iso.conf"
myCONF_DEFAULT_FILE="iso/installer/iso.conf.dist"

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    sudo ./$0
    exit
fi

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

# Let's clean up at the end or if something goes wrong ...
function fuCLEANUP {
rm -rf $myTMP $myTPOTDIR $myPFXFILE $myNTPCONFFILE $myCONF_FILE
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

# Let's ask if the user wants to run the script ...
dialog --backtitle "$myBACKTITLE" --title "[ Continue? ]" --yesno "\nDownload latest supported Debian Mini ISO and build the T-Pot Install Image." 8 50
mySTART=$?
if [ "$mySTART" = "1" ];
  then
    exit
fi

# Let's load the default config file
if [ -f $myCONF_DEFAULT_FILE ];
  then
    source $myCONF_DEFAULT_FILE
fi

# Let's ask the user for a proxy ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ Proxy Settings ]" --yesno "\nDo you want to configure a proxy?" 7 50
  myCONF_PROXY_USE=$?
  if [ "$myCONF_PROXY_USE" = "0" ]
    then
      myIPRESULT="false"
      while [ "$myIPRESULT" = "false" ];
        do
          myCONF_PROXY_IP=$(dialog --backtitle "$myBACKTITLE" --no-cancel --title "Proxy IP?" --inputbox "" 7 50 "$myCONF_PROXY_IP" 3>&1 1>&2 2>&3 3>&-)
          if valid_ip $myCONF_PROXY_IP; then myIPRESULT="true"; fi
      done
      myPORTRESULT="false"
      while [ "$myPORTRESULT" = "false" ];
        do
          myCONF_PROXY_PORT=$(dialog --backtitle "$myBACKTITLE" --no-cancel --title "Proxy Port (i.e. 3128)?" --inputbox "" 7 50 "$myCONF_PROXY_PORT" 3>&1 1>&2 2>&3 3>&-)
          if [[ $myCONF_PROXY_PORT =~ ^-?[0-9]+$ ]] && [ $myCONF_PROXY_PORT -gt 0 ] && [ $myCONF_PROXY_PORT -lt 65536 ]; then myPORTRESULT="true"; fi
      done
      sed -i.bak 's#d-i mirror/http/proxy.*#d-i mirror/http/proxy string http://'$myCONF_PROXY_IP':'$myCONF_PROXY_PORT'/#' $myTPOTSEED
      break
    else
      myCONF_PROXY_IP=""
      myCONF_PROXY_PORT=""
      break
  fi
done

# Let's ask the user for 802.1x data ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ Need 802.1x auth? ]" --yesno "\nDo you want to add a 802.1x host certificate?" 7 50
  myCONF_PFX_USE=$?
  if [ "$myCONF_PFX_USE" = "0" ]
    then
      myCONF_PFX_FILE=$(dialog --backtitle "$myBACKTITLE" --fselect "$myCONF_PFX_FILE" 15 50 3>&1 1>&2 2>&3 3>&-)
      if [ -f "$myCONF_PFX_FILE" ]
        then
          cp $myCONF_PFX_FILE $myPFXFILE
          dialog --backtitle "$myBACKTITLE" --title "[ Password protected? ]" --yesno "\nDoes the certificate need your password?" 7 50
          myCONF_PFX_PW_USE=$?
          if [ "$myCONF_PFX_PW_USE" = "0" ]
            then
              myCONF_PFX_PW=$(dialog --backtitle "$myBACKTITLE" --no-cancel --inputbox "Password?" 7 50 3>&1 1>&2 2>&3 3>&-)
	    else
	      myCONF_PFX_PW=""
          fi
          myCONF_PFX_HOST_ID=$(dialog --backtitle "$myBACKTITLE" --no-cancel --inputbox "Host ID?" 7 50 "$myCONF_PFX_HOST_ID" 3>&1 1>&2 2>&3 3>&-)
          break
        else
          dialog --backtitle "$myBACKTITLE" --title "[ Try again! ]" --msgbox "\nThis is no regular file." 7 50;
      fi
    else
      myCONF_PFX_FILE=""
      myCONF_PFX_HOST_ID=""
      myCONF_PFX_PW=""
      break
  fi
done

# Let's ask the user for a ntp server ...
while true;
do
  dialog --backtitle "$myBACKTITLE" --title "[ NTP server? ]" --yesno "\nDo you want to configure a ntp server?" 7 50
  myCONF_NTP_USE=$?
  if [ "$myCONF_NTP_USE" = "0" ]
    then
      myIPRESULT="false"
      while [ "$myIPRESULT" = "false" ];
        do
          myCONF_NTP_IP=$(dialog --backtitle "$myBACKTITLE" --no-cancel --title "NTP IP?" --inputbox "" 7 50 "$myCONF_NTP_IP" 3>&1 1>&2 2>&3 3>&-)
          if valid_ip $myCONF_NTP_IP; then myIPRESULT="true"; fi
      done
tee $myNTPCONFFILE <<EOF
driftfile /var/lib/ntp/ntp.drift

statistics loopstats peerstats clockstats
filegen loopstats file loopstats type day enable
filegen peerstats file peerstats type day enable
filegen clockstats file clockstats type day enable

server $myCONF_NTP_IP

restrict -4 default kod notrap nomodify nopeer noquery
restrict -6 default kod notrap nomodify nopeer noquery
restrict 127.0.0.1
restrict ::1
EOF

      break
    else
      myCONF_NTP_IP=""
      break
  fi
done

# Let's write the config file
if [ "$myCONF_PROXY_USE" == "0" ] || [ "$myCONF_PFX_USE" == "0" ] || [ "$myCONF_NTP_USE" == "0" ];
  then
    echo "# makeiso configuration file" > $myCONF_FILE
    echo "myCONF_PROXY_USE=\"$myCONF_PROXY_USE\"" >> $myCONF_FILE
    echo "myCONF_PROXY_IP=\"$myCONF_PROXY_IP\"" >> $myCONF_FILE
    echo "myCONF_PROXY_PORT=\"$myCONF_PROXY_PORT\"" >> $myCONF_FILE
    echo "myCONF_PFX_USE=\"$myCONF_PFX_USE\"" >> $myCONF_FILE
    echo "myCONF_PFX_FILE=\"/root/installer/keys/8021x.pfx\"" >> $myCONF_FILE
    echo "myCONF_PFX_PW_USE=\"$myCONF_PFX_PW_USE\"" >> $myCONF_FILE
    echo "myCONF_PFX_PW=\"$myCONF_PFX_PW\"" >> $myCONF_FILE
    echo "myCONF_PFX_HOST_ID=\"$myCONF_PFX_HOST_ID\"" >> $myCONF_FILE
    echo "myCONF_NTP_USE=\"$myCONF_NTP_USE\"" >> $myCONF_FILE
    echo "myCONF_NTP_IP=\"$myCONF_NTP_IP\"" >> $myCONF_FILE
    echo "myCONF_NTP_CONF_FILE=\"/root/installer/ntp.conf\"" >> $myCONF_FILE
fi

# Let's download Debian Minimal ISO
if [ ! -f $myMINIISO ]
  then
    wget $myMINIISOLINK --progress=dot 2>&1 | awk '{print $7+0} fflush()' | dialog --backtitle "$myBACKTITLE" --title "[ Downloading Debian ... ]" --gauge "" 5 70;
    echo 100 | dialog --backtitle "$myBACKTITLE" --title "[ Downloading Debian ... Done! ]" --gauge "" 5 70;
  else
    dialog --infobox "Using previously downloaded .iso ..." 3 50;
fi

# Let's loop mount it and copy all contents
mkdir -p $myTMP $myTPOTDIR
mount -o loop $myMINIISO $myTMP
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
xorrisofs -gui -D -r -V "T-Pot" -cache-inodes -J -l -b isolinux.bin -c boot.cat -no-emul-boot -boot-load-size 4 -boot-info-table -o ../$myTPOTISO ../$myTPOTDIR 2>&1 | awk '{print $1+0} fflush()' | cut -f1 -d"." | dialog --backtitle "$myBACKTITLE" --title "[ Building T-Pot .iso ... ]" --gauge "" 5 70 0
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

dialog --clear

exit 0
