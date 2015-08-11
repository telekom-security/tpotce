#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# .ISO maker                                           #
#                                                      #
# v0.14 by mo, DTAG, 2015-08-11                        #
########################################################

# Let's define some global vars
myUBUNTULINK="http://releases.ubuntu.com/14.04.3/ubuntu-14.04.3-server-amd64.iso"
myUBUNTUISO="ubuntu-14.04.3-server-amd64.iso"
myTPOTCEISO="tpotce.iso"
myTPOTCEDIR="tpotceiso"
myTMP="tmp"
myDEV=$1

# Let's create a function for colorful output
fuECHO () {
  local myRED=1
  local myWHT=7
  tput setaf $myRED
  echo $1 "$2"
  tput setaf $myWHT
}

# Let's install all the packages we need
fuECHO "### Installing packages."
apt-get update -y
apt-get install genisoimage syslinux syslinux-utils -y

# Let's get Ubuntu 14.04.2 as .iso
fuECHO "### Downloading Ubuntu 14.04.3."
if [ ! -f $myUBUNTUISO ]
  then wget $myUBUNTULINK;
  else fuECHO "### Found it locally.";
fi

# Let's loop mount it and copy all contents
fuECHO "### Mounting .iso and copying all contents."
mkdir -p $myTMP $myTPOTCEDIR
losetup /dev/loop0 $myUBUNTUISO
mount /dev/loop0 $myTMP
cp -rT $myTMP $myTPOTCEDIR
chmod 777 -R $myTPOTCEDIR
umount $myTMP
losetup -d /dev/loop0

# Let's add the files for the automated install
fuECHO "### Adding the automated install files."
mkdir -p $myTPOTCEDIR/tpotce
cp installer/* -R $myTPOTCEDIR/tpotce/
cp isolinux/* $myTPOTCEDIR/isolinux/
cp kickstart/* $myTPOTCEDIR/tpotce/
cp preseed/* $myTPOTCEDIR/tpotce/
chmod 777 -R $myTPOTCEDIR

# Let's create the new .iso
fuECHO "### Now creating the .iso."
cd $myTPOTCEDIR
mkisofs -D -r -V "T-Pot CE" -cache-inodes -J -l -b isolinux/isolinux.bin -c isolinux/boot.cat -no-emul-boot -boot-load-size 4 -boot-info-table -o ../$myTPOTCEISO ../$myTPOTCEDIR
cd ..
isohybrid $myTPOTCEISO

# Let's clean up
fuECHO "### Cleaning up."
rm -rf $myTMP $myTPOTCEDIR

# Let's write the image to $myDEV or show instructions
if [ -b $myDEV ] && [ ! -z $1 ]
then
  fuECHO "### Found a block device on $myDEV"
  fuECHO "### Writing image to device. Please wait..."
  dd bs=1M if="$myTPOTCEISO" of="$myDEV"
else
  fuECHO "### Install to usb stick"
  fuECHO "###### Show devices:    df or fdisk -l"
  fuECHO "###### Write to device: dd bs=1M if="$myTPOTCEISO" of=<path to device>"
fi

# Done
fuECHO "### Done."
exit 0
