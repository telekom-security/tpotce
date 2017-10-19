#!/bin/bash
#
# p0f - build script
# ------------------
#
# Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>
#
# Distributed under the terms and conditions of GNU LGPL.
#

PROGNAME="p0f"
VERSION="3.09b"

test "$CC" = "" && CC="gcc"

BASIC_CFLAGS="-Wall -Wno-format -I/usr/local/include/ \
              -I/opt/local/include/ -DVERSION=\"$VERSION\" $CFLAGS"

BASIC_LDFLAGS="-L/usr/local/lib/ -L/opt/local/lib $LDFLAGS"

USE_CFLAGS="-fstack-protector-all -fPIE -D_FORTIFY_SOURCE=2 -g -ggdb \
            $BASIC_CFLAGS"

USE_LDFLAGS="-Wl,-z,relro -pie $BASIC_LDFLAGS"

if [ "$OSTYPE" = "cygwin" ]; then
  USE_LIBS="-lwpcap $LIBS"
elif [ "$OSTYPE" = "solaris" ]; then
  USE_LIBS="-lsocket -lnsl $LIBS"
else
  USE_LIBS="-lpcap -ljansson $LIBS"
fi

OBJFILES="api.c process.c fp_tcp.c fp_mtu.c fp_http.c readfp.c"

echo "Welcome to the build script for $PROGNAME $VERSION!"
echo "Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>"
echo

if [ "$#" -gt "1" ]; then

  echo "[-] Please specify one build target at a time."
  exit 1

fi

if [ "$1" = "clean" -o "$1" = "publish" ]; then

  echo "[*] Cleaning up build environment..."
  rm -f -- "$PROGNAME" *.exe *.o a.out *~ core core.[1-9][0-9]* *.stackdump COMPILER-WARNINGS 2>/dev/null

  ( cd tools && make clean ) &>/dev/null

  if [ "$1" = "publish" ]; then

    if [ ! "`basename -- \"$PWD\"`" = "$PROGNAME" ]; then
      echo "[-] Invalid working directory."
      exit 1
    fi

    if [ ! "$HOSTNAME" = "raccoon" ]; then
      echo "[-] You are not my real dad!"
      exit 1
    fi

    TARGET="/var/www/lcamtuf/p0f3/$PROGNAME-devel.tgz"

    echo "[*] Creating $TARGET..."

    cd ..
    rm -rf "$PROGNAME-$VERSION"
    cp -pr "$PROGNAME" "$PROGNAME-$VERSION"
    tar cfvz "$TARGET" "$PROGNAME-$VERSION"

  fi

  echo "[+] All done!"

  exit 0

elif [ "$1" = "all" -o "$1" = "" ]; then

  echo "[+] Configuring production build."
  BASIC_CFLAGS="$BASIC_CFLAGS -O3"
  USE_CFLAGS="$USE_CFLAGS -O3"

elif [ "$1" = "debug" ]; then

  echo "[+] Configuring debug build."
  BASIC_CFLAGS="$BASIC_CFLAGS -DDEBUG_BUILD=1"
  USE_CFLAGS="$USE_CFLAGS -DDEBUG_BUILD=1"
  
else

  echo "[-] Unrecognized build target '$1', sorry."
  exit 1

fi

rm -f COMPILER-WARNINGS 2>/dev/null

echo -n "[*] Checking for a sane build environment... "

if ls -ld ./ | grep -q '^d.......w'; then

  echo "FAIL (bad permissions)"
  echo
  echo "Duuude, don't build stuff in world-writable directories."
  echo
  exit 1

fi

TMP=".build-$$"

rm -f "$TMP" 2>/dev/null

if [ -f "$TMP" ]; then

  echo "FAIL (can't delete)"
  echo
  echo "Check directory permissions and try again."
  echo
  exit 1

fi

touch "$TMP" 2>/dev/null

if [ ! -f "$TMP" ]; then

  echo "FAIL (can't create)"
  echo
  echo "Check directory permissions and try again."
  echo
  exit 1

fi

if [ ! -s "$PROGNAME.c" ]; then

  echo "FAIL (no source)"
  echo
  echo "I'm no doctor, but I think the source code is missing from CWD."
  echo
  exit 1

fi

echo "OK"

echo -n "[*] Checking for working GCC... "

rm -f "$TMP" || exit 1

echo "int main() { return 0; }" >"$TMP.c" || exit 1
$CC $BASIC_CFLAGS $BASIC_LDFLAGS "$TMP.c" -o "$TMP" &>"$TMP.log"

if [ ! -x "$TMP" ]; then

  echo "FAIL"
  echo
  echo "Your compiler can't produce working binaries. You need a functioning install of"
  echo "GCC and libc (including development headers) to continue. If you have these,"
  echo "try setting CC, CFLAGS, and LDFLAGS appropriately."
  echo
  echo "Output from an attempt to execute GCC:"
  cat "$TMP.log" | head -10
  echo
  rm -f "$TMP" "$TMP.log" "$TMP.c"
  exit 1

fi

echo "OK"

echo -n "[*] Checking for *modern* GCC... "

rm -f "$TMP" "$TMP.c" "$TMP.log" || exit 1

echo "int main() { return 0; }" >"$TMP.c" || exit 1
$CC $USE_CFLAGS $USE_LDFLAGS "$TMP.c" -o "$TMP" &>"$TMP.log"

if [ ! -x "$TMP" ]; then

  echo "FAIL (but we can live with it)"
  USE_CFLAGS="$BASIC_CFLAGS"
  USE_LDFLAGS="$BASIC_LDFLAGS"

else

  echo "OK"

fi

echo -n "[*] Checking if memory alignment is required... "

rm -f "$TMP" "$TMP.c" "$TMP.log" || exit 1

echo -e "#include \"types.h\"\nvolatile u8 tmp[6]; int main() { printf(\"%d\x5cn\", *(u32*)(tmp+1)); return 0; }" >"$TMP.c" || exit 1
$CC $USE_CFLAGS $USE_LDFLAGS "$TMP.c" -o "$TMP" &>"$TMP.log"

if [ ! -x "$TMP" ]; then

  echo "FAIL"
  echo
  echo "Well, something went horribly wrong, sorry. Here's the output from GCC:"
  echo
  cat "$TMP.log"
  echo
  echo "Sorry! You may want to ping <lcamtuf@coredump.cx> about this."
  echo
  rm -f "$TMP.log"
  exit 1

else

  ulimit -c 0 &>/dev/null
  ./"$TMP" &>/dev/null

  if [ "$?" = "0" ]; then

    echo "nope"

  else

    echo "yes"
    USE_CFLAGS="$USE_CFLAGS -DALIGN_ACCESS=1"

  fi

fi


echo -n "[*] Checking for working libpcap... "

rm -f "$TMP" "$TMP.c" "$TMP.log" || exit 1

echo -e "#include <pcap.h>\nint main() { char i[PCAP_ERRBUF_SIZE]; pcap_lookupdev(i); return 0; }" >"$TMP.c" || exit 1
$CC $USE_CFLAGS $USE_LDFLAGS "$TMP.c" -o "$TMP" $USE_LIBS &>"$TMP.log"

if [ ! -x "$TMP" ]; then
  echo "FAIL"
  echo

  if [ "$OSTYPE" = "cygwin" ]; then

    echo "You need a functioning install of winpcap. Download both of those:"
    echo 
    echo "  Main library    : http://www.winpcap.org/install/default.htm"
    echo "  Developer tools : http://www.winpcap.org/devel.htm"
    echo
    echo "Under cygwin, copy the contents of wpdpack/include to /usr/include/, and"
    echo "wpdpack/lib to /lib/. At that point, you should be able to build p0f."
    echo

  else

    echo "You need a functioning installation of libpcap (including development headers)."
    echo "You can download it from here:"
    echo 
    echo "  http://www.tcpdump.org/#latest-release"
    echo

  fi

  echo "If you have the library installed at an unorthodox location, try setting CFLAGS"
  echo "and LDFLAGS to point us in the right direction."
  echo
  echo "Output from an attempt to compile sample program:"
  cat "$TMP.log" | head -10
  echo
  rm -f "$TMP" "$TMP.log" "$TMP.c"
  exit 1

fi

echo "OK"

echo -n "[*] Checking for working BPF... "

rm -f "$TMP" "$TMP.c" "$TMP.log" || exit 1

echo -e "#include <pcap.h>\n#include <pcap-bpf.h>\nint main() { return 0; }" >"$TMP.c" || exit 1
$CC $USE_CFLAGS $USE_LDFLAGS "$TMP.c" -o "$TMP" $USE_LIBS &>"$TMP.log"

if [ ! -x "$TMP" ]; then

  rm -f "$TMP" "$TMP.c" "$TMP.log" || exit 1

  echo -e "#include <pcap.h>\n#include <net/bpf.h>\nint main() { return 0; }" >"$TMP.c" || exit 1
  $CC $USE_CFLAGS $USE_LDFLAGS "$TMP.c" -o "$TMP" $USE_LIBS &>"$TMP.log"

  if [ ! -x "$TMP" ]; then
    echo "FAIL"
    echo
    echo "Could not find a working version of pcap-bpf.h or net/bpf.h on your system."
    echo "If it's available in a non-standard directory, set CFLAGS accordingly; if it"
    echo "lives under a different name, you may need to edit the source and recompile."
    echo

    rm -f "$TMP" "$TMP.log" "$TMP.c"
    exit 1

  fi

  USE_CFLAGS="$USE_CFLAGS -DNET_BPF=1"

fi

echo "OK"

rm -f "$TMP" "$TMP.log" "$TMP.c" || exit 1

echo "[+] Okay, you seem to be good to go. Fingers crossed!"

echo -n "[*] Compiling $PROGNAME... "

rm -f "$PROGNAME" || exit 1

$CC $USE_CFLAGS $USE_LDFLAGS "$PROGNAME.c" $OBJFILES -o "$PROGNAME" $USE_LIBS &>"$TMP.log"

if [ ! -x "$PROGNAME" ]; then

  echo "FAIL"
  echo
  echo "Well, something went horribly wrong, sorry. Here's the output from GCC:"
  echo
  cat "$TMP.log"
  echo
  echo "Sorry! You may want to ping <lcamtuf@coredump.cx> about this."
  echo
  rm -f "$TMP.log"
  exit 1

fi

if [ -s "$TMP.log" ]; then

  echo "OK (see COMPILER-WARNINGS)"
  mv "$TMP.log" COMPILER-WARNINGS

  test "$1" = "debug" && cat COMPILER-WARNINGS

else

  rm -f "$TMP.log"
  echo "OK"

fi

echo
echo "Well, that's it. Be sure to review README. If you run into any problems, you"
echo "can reach the author at <lcamtuf@coredump.cx>."
echo

exit 0
