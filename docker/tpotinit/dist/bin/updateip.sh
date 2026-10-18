#!/bin/bash
# Let's add the first local ip to the /tmp/etc/issue and external ip to ews.ip file
# If the external IP cannot be detected, the internal IP will be inherited.
myUUID=$(cat /data/uuid)
myLOCALIP=$(ip address show | awk '/inet .*brd/{split($2,a,"/"); print a[1]; exit}')
myEXTIP=$(/opt/tpot/bin/myip.sh)
if [ "$myEXTIP" = "" ];
  then
    myEXTIP=$myLOCALIP
fi

myBLACKHOLE_STATUS=$(ip r | grep "blackhole" -c)
if [ "$myBLACKHOLE_STATUS" -gt "500" ];
  then
    myBLACKHOLE_STATUS="| [1;34mBLACKHOLE: [ [0;37mENABLED[1;34m ][0m"
  else
    myBLACKHOLE_STATUS="| [1;34mBLACKHOLE: [ [1;30mDISABLED[1;34m ][0m"
fi

# The running T-Pot is the image tag TPOT_VERSION of .env (env_file of tpotinit, required by
# env.schema.yml); only the characters of a tag go into the issue file (agetty reads escapes)
myVERSION="${TPOT_VERSION//[^A-Za-z0-9._+-]/}"

# Build issue
echo "[H[2J" > /tmp/etc/issue
echo "T-Pot${myVERSION:+ ${myVERSION}}" >> /tmp/etc/issue
echo >> /tmp/etc/issue
echo ",---- [ [1;34m\n[0m ] [ [0;34m\d[0m ] [ [1;30m\t[0m ]" >> /tmp/etc/issue
echo "|" >> /tmp/etc/issue
echo "| [1;34mIP: $myLOCALIP ($myEXTIP)[0m" >> /tmp/etc/issue
echo "| [0;34mSSH: ssh -l tsec -p 64295 $myLOCALIP[0m" >> /tmp/etc/issue
#if [ "$myCHECKIFSENSOR" == "0" ];
#  then
echo "| [1;30mWEB: https://$myLOCALIP:64297[0m" >> /tmp/etc/issue
#fi
echo "| [0;37mADMIN: https://$myLOCALIP:64294[0m" >> /tmp/etc/issue
echo "$myBLACKHOLE_STATUS" >> /tmp/etc/issue
echo "|" >> /tmp/etc/issue
echo "\`----" >> /tmp/etc/issue
echo >> /tmp/etc/issue
# the text on stdin into the file $1, in place (ews.ip is bind mounted: no rename) through an
# O_NOFOLLOW descriptor: a link planted in its place is refused, never followed; truncated, written,
# synced; a new file gets 0660
# >>> write in place >>>
myWRITE_IN_PLACE='import os, stat, sys
data = sys.stdin.buffer.read()
handle = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o660)
try:
    if not stat.S_ISREG(os.fstat(handle).st_mode):
        sys.exit("no regular file: " + sys.argv[1])
    os.ftruncate(handle, 0)
    os.write(handle, data)
    os.fsync(handle)
finally:
    os.close(handle)'
# <<< write in place <<<
printf '[MAIN]\nip = %s\n' "$myEXTIP" | python3 -c "${myWRITE_IN_PLACE}" /data/ews/conf/ews.ip \
  || echo "# Warning: /data/ews/conf/ews.ip is a link or no regular file, it was not written."
printf 'HONEY_UUID=%s\nMY_EXTIP=%s\nMY_INTIP=%s\nMY_HOSTNAME=%s\n' "$myUUID" "$myEXTIP" "$myLOCALIP" "$HOSTNAME" \
  | python3 -c "${myWRITE_IN_PLACE}" /data/tpot/etc/compose/elk_environment \
  || echo "# Warning: /data/tpot/etc/compose/elk_environment is a link or no regular file, it was not written."

# never through a link (the folder is writable for the group tpot): opened with O_NOFOLLOW and
# changed through that descriptor
python3 -c 'import grp, os, pwd, stat, sys
handle = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
try:
    if stat.S_ISREG(os.fstat(handle).st_mode):
        os.fchmod(handle, 0o660)
        os.fchown(handle, pwd.getpwnam("tpot").pw_uid, grp.getgrnam("tpot").gr_gid)
finally:
    os.close(handle)' /data/ews/conf/ews.ip || echo "# Warning: /data/ews/conf/ews.ip is no regular file or cannot be changed."
