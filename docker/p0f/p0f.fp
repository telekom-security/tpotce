;
; p0f - fingerprint database
; --------------------------
;
; See section 5 in the README for a detailed discussion of the format used here.
;
; Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>
;
; Distributed under the terms and conditions of GNU LGPL.
;

classes = win,unix,other

; ==============
; MTU signatures
; ==============

[mtu]

; The most common values, used by Ethernet-homed systems, PPP over POTS, PPPoA
; DSL, etc:

label = Ethernet or modem
sig   = 576
sig   = 1500

; Common DSL-specific values (1492 is canonical for PPPoE, but ISPs tend to
; horse around a bit):

label = DSL
sig   = 1452
sig   = 1454
sig   = 1492

; Miscellanous tunnels (including VPNs, IPv6 tunneling, etc):

label = GIF
sig   = 1240
sig   = 1280

label = generic tunnel or VPN
sig   = 1300
sig   = 1400
sig   = 1420
sig   = 1440
sig   = 1450
sig   = 1460

label = IPSec or GRE
sig   = 1476

label = IPIP or SIT
sig   = 1480

label = PPTP
sig   = 1490

; Really exotic stuff:

label = AX.25 radio modem
sig   = 256

label = SLIP
sig   = 552

label = Google
sig   = 1470

label = VLAN
sig   = 1496

label = Ericsson HIS modem
sig   = 1656

label = jumbo Ethernet
sig   = 9000

; Loopback interfaces on Linux and other systems:

label = loopback
sig   = 3924
sig   = 16384
sig   = 16436

; ==================
; TCP SYN signatures
; ==================

[tcp:request]

; -----
; Linux
; -----

label = s:unix:Linux:3.11 and newer
sig   = *:64:0:*:mss*20,10:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*20,7:mss,sok,ts,nop,ws:df,id+:0

label = s:unix:Linux:3.1-3.10
sig   = *:64:0:*:mss*10,4:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*10,5:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*10,6:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*10,7:mss,sok,ts,nop,ws:df,id+:0

; Fun fact: 2.6 with ws=7 seems to be really common for Amazon EC2, while 8 is
; common for Yahoo and Twitter. There seem to be some other (rare) uses, though,
; so not I'm not flagging these signatures in a special way.

label = s:unix:Linux:2.6.x
sig   = *:64:0:*:mss*4,6:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*4,7:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*4,8:mss,sok,ts,nop,ws:df,id+:0

label = s:unix:Linux:2.4.x
sig   = *:64:0:*:mss*4,0:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*4,1:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*4,2:mss,sok,ts,nop,ws:df,id+:0

; No real traffic seen for 2.2 & 2.0, signatures extrapolated from p0f2 data:

label = s:unix:Linux:2.2.x
sig   = *:64:0:*:mss*11,0:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*20,0:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*22,0:mss,sok,ts,nop,ws:df,id+:0

label = s:unix:Linux:2.0
sig   = *:64:0:*:mss*12,0:mss::0
sig   = *:64:0:*:16384,0:mss::0

; Just to keep people testing locally happy (IPv4 & IPv6):

label = s:unix:Linux:3.x (loopback)
sig   = *:64:0:16396:mss*2,4:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:16376:mss*2,4:mss,sok,ts,nop,ws:df,id+:0

label = s:unix:Linux:2.6.x (loopback)
sig   = *:64:0:16396:mss*2,2:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:16376:mss*2,2:mss,sok,ts,nop,ws:df,id+:0

label = s:unix:Linux:2.4.x (loopback)
sig   = *:64:0:16396:mss*2,0:mss,sok,ts,nop,ws:df,id+:0

label = s:unix:Linux:2.2.x (loopback)
sig   = *:64:0:3884:mss*8,0:mss,sok,ts,nop,ws:df,id+:0

; Various distinctive flavors of Linux:

label = s:unix:Linux:2.6.x (Google crawler)
sig   = 4:64:0:1430:mss*4,6:mss,sok,ts,nop,ws::0

label = s:unix:Linux:(Android)
sig   = *:64:0:*:mss*44,1:mss,sok,ts,nop,ws:df,id+:0
sig   = *:64:0:*:mss*44,3:mss,sok,ts,nop,ws:df,id+:0

; Catch-all rules:

label = g:unix:Linux:3.x
sig   = *:64:0:*:mss*10,*:mss,sok,ts,nop,ws:df,id+:0

label = g:unix:Linux:2.4.x-2.6.x
sig   = *:64:0:*:mss*4,*:mss,sok,ts,nop,ws:df,id+:0

label = g:unix:Linux:2.2.x-3.x
sig   = *:64:0:*:*,*:mss,sok,ts,nop,ws:df,id+:0

label = g:unix:Linux:2.2.x-3.x (no timestamps)
sig   = *:64:0:*:*,*:mss,nop,nop,sok,nop,ws:df,id+:0

label = g:unix:Linux:2.2.x-3.x (barebone)
sig   = *:64:0:*:*,0:mss:df,id+:0

; -------
; Windows
; -------

label = s:win:Windows:XP
sig   = *:128:0:*:16384,0:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,0:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,0:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,1:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,2:mss,nop,ws,nop,nop,sok:df,id+:0

label = s:win:Windows:7 or 8
sig   = *:128:0:*:8192,0:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:8192,2:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:8192,8:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:8192,2:mss,nop,ws,sok,ts:df,id+:0

; Robots with distinctive fingerprints:

label = s:win:Windows:7 (Websense crawler)
sig   = *:64:0:1380:mss*4,6:mss,nop,nop,ts,nop,ws:df,id+:0
sig   = *:64:0:1380:mss*4,7:mss,nop,nop,ts,nop,ws:df,id+:0

; Catch-all:

label = g:win:Windows:NT kernel 5.x
sig   = *:128:0:*:16384,*:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,*:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:16384,*:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,*:mss,nop,ws,nop,nop,sok:df,id+:0

label = g:win:Windows:NT kernel 6.x
sig   = *:128:0:*:8192,*:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:8192,*:mss,nop,ws,nop,nop,sok:df,id+:0

label = g:win:Windows:NT kernel
sig   = *:128:0:*:*,*:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:*,*:mss,nop,ws,nop,nop,sok:df,id+:0

; ------
; Mac OS
; ------

label = s:unix:Mac OS X:10.x
sig   = *:64:0:*:65535,1:mss,nop,ws,nop,nop,ts,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,3:mss,nop,ws,nop,nop,ts,sok,eol+1:df,id+:0

label = s:unix:MacOS X:10.9 or newer (sometimes iPhone or iPad)
sig   = *:64:0:*:65535,4:mss,nop,ws,nop,nop,ts,sok,eol+1:df,id+:0

label = s:unix:iOS:iPhone or iPad
sig   = *:64:0:*:65535,2:mss,nop,ws,nop,nop,ts,sok,eol+1:df,id+:0

; Catch-all rules:

label = g:unix:Mac OS X:
sig   = *:64:0:*:65535,*:mss,nop,ws,nop,nop,ts,sok,eol+1:df,id+:0

; -------
; FreeBSD
; -------

label = s:unix:FreeBSD:9.x or newer
sig   = *:64:0:*:65535,6:mss,nop,ws,sok,ts:df,id+:0

label = s:unix:FreeBSD:8.x
sig   = *:64:0:*:65535,3:mss,nop,ws,sok,ts:df,id+:0

; Catch-all rules:

label = g:unix:FreeBSD:
sig   = *:64:0:*:65535,*:mss,nop,ws,sok,ts:df,id+:0

; -------
; OpenBSD
; -------

label = s:unix:OpenBSD:3.x
sig   = *:64:0:*:16384,0:mss,nop,nop,sok,nop,ws,nop,nop,ts:df,id+:0

label = s:unix:OpenBSD:4.x-5.x
sig   = *:64:0:*:16384,3:mss,nop,nop,sok,nop,ws,nop,nop,ts:df,id+:0

; -------
; Solaris
; -------

label = s:unix:Solaris:8
sig   = *:64:0:*:32850,1:nop,ws,nop,nop,ts,nop,nop,sok,mss:df,id+:0

label = s:unix:Solaris:10
sig   = *:64:0:*:mss*34,0:mss,nop,ws,nop,nop,sok:df,id+:0

; -------
; OpenVMS
; -------

label = s:unix:OpenVMS:8.x
sig   = 4:128:0:1460:mtu*2,0:mss,nop,ws::0

label = s:unix:OpenVMS:7.x
sig   = 4:64:0:1460:61440,0:mss,nop,ws::0

; --------
; NeXTSTEP
; --------

label = s:other:NeXTSTEP:
sig   = 4:64:0:1024:mss*4,0:mss::0

; -----
; Tru64
; -----

label = s:unix:Tru64:4.x
sig   = 4:64:0:1460:32768,0:mss,nop,ws:df,id+:0

; ----
; NMap
; ----

label = s:!:NMap:SYN scan
sys   = @unix,@win
sig   = *:64-:0:1460:1024,0:mss::0
sig   = *:64-:0:1460:2048,0:mss::0
sig   = *:64-:0:1460:3072,0:mss::0
sig   = *:64-:0:1460:4096,0:mss::0

label = s:!:NMap:OS detection
sys   = @unix,@win
sig   = *:64-:0:265:512,0:mss,sok,ts:ack+:0
sig   = *:64-:0:0:4,10:sok,ts,ws,eol+0:ack+:0
sig   = *:64-:0:1460:1,10:ws,nop,mss,ts,sok:ack+:0
sig   = *:64-:0:536:16,10:mss,sok,ts,ws,eol+0:ack+:0
sig   = *:64-:0:640:4,5:ts,nop,nop,ws,nop,mss:ack+:0
sig   = *:64-:0:1400:63,0:mss,ws,sok,ts,eol+0:ack+:0
sig   = *:64-:0:265:31337,10:ws,nop,mss,ts,sok:ack+:0
sig   = *:64-:0:1460:3,10:ws,nop,mss,sok,nop,nop:ecn,uptr+:0

; -----------
; p0f-sendsyn
; -----------

; These are intentionally goofy, to avoid colliding with any sensible real-world
; stacks. Do not tag these signatures as userspace, unless you want p0f to hide
; the responses!

label = s:unix:p0f:sendsyn utility
sig   = *:192:0:1331:1337,0:mss,nop,eol+18::0
sig   = *:192:0:1331:1337,0:mss,ts,nop,eol+8::0
sig   = *:192:0:1331:1337,5:mss,ws,nop,eol+15::0
sig   = *:192:0:1331:1337,0:mss,sok,nop,eol+16::0
sig   = *:192:0:1331:1337,5:mss,ws,ts,nop,eol+5::0
sig   = *:192:0:1331:1337,0:mss,sok,ts,nop,eol+6::0
sig   = *:192:0:1331:1337,5:mss,ws,sok,nop,eol+13::0
sig   = *:192:0:1331:1337,5:mss,ws,sok,ts,nop,eol+3::0

; -------------
; Odds and ends
; -------------

label = s:other:Blackberry:
sig   = *:128:0:1452:65535,0:mss,nop,nop,sok,nop,nop,ts::0

label = s:other:Nintendo:3DS
sig   = *:64:0:1360:32768,0:mss,nop,nop,sok:df,id+:0

label = s:other:Nintendo:Wii
sig   = 4:64:0:1460:32768,0:mss,nop,nop,sok:df,id+:0

label = s:unix:BaiduSpider:
sig   = *:64:0:1460:mss*4,7:mss,sok,nop,nop,nop,nop,nop,nop,nop,nop,nop,nop,nop,ws:df,id+:0
sig   = *:64:0:1460:mss*4,2:mss,sok,nop,nop,nop,nop,nop,nop,nop,nop,nop,nop,nop,ws:df,id+:0

; ======================
; TCP SYN+ACK signatures
; ======================

[tcp:response]

; -----
; Linux
; -----

; The variation here is due to ws, sok, or ts being adaptively removed if the
; client initiating the connection doesn't support them. Use tools/p0f-sendsyn
; to get a full set of up to 8 signatures.


label = s:unix:Linux:3.x
sig   = *:64:0:*:mss*10,0:mss:df:0
sig   = *:64:0:*:mss*10,0:mss,sok,ts:df:0
sig   = *:64:0:*:mss*10,0:mss,nop,nop,ts:df:0
sig   = *:64:0:*:mss*10,0:mss,nop,nop,sok:df:0
sig   = *:64:0:*:mss*10,*:mss,nop,ws:df:0
sig   = *:64:0:*:mss*10,*:mss,sok,ts,nop,ws:df:0
sig   = *:64:0:*:mss*10,*:mss,nop,nop,ts,nop,ws:df:0
sig   = *:64:0:*:mss*10,*:mss,nop,nop,sok,nop,ws:df:0

label = s:unix:Linux:2.4-2.6
sig   = *:64:0:*:mss*4,0:mss:df:0
sig   = *:64:0:*:mss*4,0:mss,sok,ts:df:0
sig   = *:64:0:*:mss*4,0:mss,nop,nop,ts:df:0
sig   = *:64:0:*:mss*4,0:mss,nop,nop,sok:df:0

label = s:unix:Linux:2.4.x
sig   = *:64:0:*:mss*4,0:mss,nop,ws:df:0
sig   = *:64:0:*:mss*4,0:mss,sok,ts,nop,ws:df:0
sig   = *:64:0:*:mss*4,0:mss,nop,nop,ts,nop,ws:df:0
sig   = *:64:0:*:mss*4,0:mss,nop,nop,sok,nop,ws:df:0

label = s:unix:Linux:2.6.x
sig   = *:64:0:*:mss*4,*:mss,nop,ws:df:0
sig   = *:64:0:*:mss*4,*:mss,sok,ts,nop,ws:df:0
sig   = *:64:0:*:mss*4,*:mss,nop,nop,ts,nop,ws:df:0
sig   = *:64:0:*:mss*4,*:mss,nop,nop,sok,nop,ws:df:0

; -------
; Windows
; -------

label = s:win:Windows:XP
sig   = *:128:0:*:65535,0:mss:df,id+:0
sig   = *:128:0:*:65535,0:mss,nop,ws:df,id+:0
sig   = *:128:0:*:65535,0:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,0:mss,nop,nop,ts:df,id+,ts1-:0
sig   = *:128:0:*:65535,0:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:65535,0:mss,nop,ws,nop,nop,ts:df,id+,ts1-:0
sig   = *:128:0:*:65535,0:mss,nop,nop,ts,nop,nop,sok:df,id+,ts1-:0
sig   = *:128:0:*:65535,0:mss,nop,ws,nop,nop,ts,nop,nop,sok:df,id+,ts1-:0

sig   = *:128:0:*:16384,0:mss:df,id+:0
sig   = *:128:0:*:16384,0:mss,nop,ws:df,id+:0
sig   = *:128:0:*:16384,0:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:16384,0:mss,nop,nop,ts:df,id+,ts1-:0
sig   = *:128:0:*:16384,0:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:128:0:*:16384,0:mss,nop,ws,nop,nop,ts:df,id+,ts1-:0
sig   = *:128:0:*:16384,0:mss,nop,nop,ts,nop,nop,sok:df,id+,ts1-:0
sig   = *:128:0:*:16384,0:mss,nop,ws,nop,nop,ts,nop,nop,sok:df,id+,ts1-:0

label = s:win:Windows:7 or 8
sig   = *:128:0:*:8192,0:mss:df,id+:0
sig   = *:128:0:*:8192,0:mss,sok,ts:df,id+:0
sig   = *:128:0:*:8192,8:mss,nop,ws:df,id+:0
sig   = *:128:0:*:8192,0:mss,nop,nop,ts:df,id+:0
sig   = *:128:0:*:8192,0:mss,nop,nop,sok:df,id+:0
sig   = *:128:0:*:8192,8:mss,nop,ws,sok,ts:df,id+:0
sig   = *:128:0:*:8192,8:mss,nop,ws,nop,nop,ts:df,id+:0
sig   = *:128:0:*:8192,8:mss,nop,ws,nop,nop,sok:df,id+:0

; -------
; FreeBSD
; -------

label = s:unix:FreeBSD:9.x
sig   = *:64:0:*:65535,6:mss,nop,ws:df,id+:0
sig   = *:64:0:*:65535,6:mss,nop,ws,sok,ts:df,id+:0
sig   = *:64:0:*:65535,6:mss,nop,ws,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,6:mss,nop,ws,nop,nop,ts:df,id+:0

label = s:unix:FreeBSD:8.x
sig   = *:64:0:*:65535,3:mss,nop,ws:df,id+:0
sig   = *:64:0:*:65535,3:mss,nop,ws,sok,ts:df,id+:0
sig   = *:64:0:*:65535,3:mss,nop,ws,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,3:mss,nop,ws,nop,nop,ts:df,id+:0

label = s:unix:FreeBSD:8.x-9.x
sig   = *:64:0:*:65535,0:mss,sok,ts:df,id+:0
sig   = *:64:0:*:65535,0:mss,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,0:mss,nop,nop,ts:df,id+:0

; -------
; OpenBSD
; -------

label = s:unix:OpenBSD:5.x
sig   = *:64:0:1460:16384,0:mss,nop,nop,sok:df,id+:0
sig   = *:64:0:1460:16384,3:mss,nop,ws:df,id+:0
sig   = *:64:0:1460:16384,3:mss,nop,nop,sok,nop,ws:df,id+:0
sig   = *:64:0:1460:16384,0:mss,nop,nop,ts:df,id+:0
sig   = *:64:0:1460:16384,0:mss,nop,nop,sok,nop,nop,ts:df,id+:0
sig   = *:64:0:1460:16384,3:mss,nop,ws,nop,nop,ts:df,id+:0
sig   = *:64:0:1460:16384,3:mss,nop,nop,sok,nop,ws,nop,nop,ts:df,id+:0

; This one resembles Windows, but almost nobody will be seeing it:
; sig   = *:64:0:1460:16384,0:mss:df,id+:0

; --------
; Mac OS X
; --------

label = s:unix:Mac OS X:10.x
sig   = *:64:0:*:65535,0:mss,nop,ws:df,id+:0
sig   = *:64:0:*:65535,0:mss,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,0:mss,nop,nop,ts:df,id+:0
sig   = *:64:0:*:65535,0:mss,nop,ws,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,0:mss,nop,ws,nop,nop,ts:df,id+:0
sig   = *:64:0:*:65535,0:mss,nop,nop,ts,sok,eol+1:df,id+:0
sig   = *:64:0:*:65535,0:mss,nop,ws,nop,nop,ts,sok,eol+1:df,id+:0

; Ditto:
; sig   = *:64:0:*:65535,0:mss:df,id+:0

; -------
; Solaris
; -------

label = s:unix:Solaris:6
sig   = 4:255:0:*:mss*7,0:mss:df,id+:0
sig   = 4:255:0:*:mss*7,0:nop,ws,mss:df,id+:0
sig   = 4:255:0:*:mss*7,0:nop,nop,ts,mss:df,id+:0
sig   = 4:255:0:*:mss*7,0:nop,nop,ts,nop,ws,mss:df,id+:0

label = s:unix:Solaris:8
sig   = *:64:0:*:mss*19,0:mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,ws,mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,nop,ts,mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,nop,sok,mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,nop,ts,nop,ws,mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,ws,nop,nop,sok,mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,nop,ts,nop,nop,sok,mss:df,id+:0
sig   = *:64:0:*:mss*19,0:nop,nop,ts,nop,ws,nop,nop,sok,mss:df,id+:0

label = s:unix:Solaris:10
sig   = *:64:0:*:mss*37,0:mss:df,id+:0
sig   = *:64:0:*:mss*37,0:mss,nop,ws:df,id+:0
sig   = *:64:0:*:mss*37,0:nop,nop,ts,mss:df,id+:0
sig   = *:64:0:*:mss*37,0:mss,nop,nop,sok:df,id+:0
sig   = *:64:0:*:mss*37,0:nop,nop,ts,mss,nop,ws:df,id+:0
sig   = *:64:0:*:mss*37,0:mss,nop,ws,nop,nop,sok:df,id+:0
sig   = *:64:0:*:mss*37,0:nop,nop,ts,mss,nop,nop,sok:df,id+:0
sig   = *:64:0:*:mss*37,0:nop,nop,ts,mss,nop,ws,nop,nop,sok:df,id+:0

; -----
; HP-UX
; -----

label = s:unix:HP-UX:11.x
sig   = *:64:0:*:32768,0:mss:df,id+:0
sig   = *:64:0:*:32768,0:mss,ws,nop:df,id+:0
sig   = *:64:0:*:32768,0:mss,nop,nop,ts:df,id+:0
sig   = *:64:0:*:32768,0:mss,nop,nop,sok:df,id+:0
sig   = *:64:0:*:32768,0:mss,ws,nop,nop,nop,ts:df,id+:0
sig   = *:64:0:*:32768,0:mss,nop,nop,sok,ws,nop:df,id+:0
sig   = *:64:0:*:32768,0:mss,nop,nop,sok,nop,nop,ts:df,id+:0
sig   = *:64:0:*:32768,0:mss,nop,nop,sok,ws,nop,nop,nop,ts:df,id+:0

; -------
; OpenVMS
; -------

label = s:other:OpenVMS:7.x
sig   = 4:64:0:1460:3993,0:mss::0
sig   = 4:64:0:1460:3993,0:mss,nop,ws::0

; -----
; Tru64
; -----

label = s:unix:Tru64:4.x
sig   = 4:64:0:1460:mss*25,0:mss,nop,ws:df,id+:0
sig   = 4:64:0:1460:mss*25,0:mss:df,id+:0

; ======================
; HTTP client signatures
; ======================

; Safari and Firefox are frequently seen using HTTP/1.0 when going through
; proxies; this is far less common for MSIE, Chrome, etc. I wildcarded some of
; the signatures accordingly.
;
; Also note that there are several proxies that mess with HTTP headers for no
; reason. For example, BlueCoat proxy appears to change 'keep-alive' to
; 'Keep-Alive' for a tiny percentage of users (why?!).

[http:request]

ua_os = Linux,Windows,iOS=[iPad],iOS=[iPhone],Mac OS X,FreeBSD,OpenBSD,NetBSD,Solaris=[SunOS]

; -------
; Firefox
; -------

label = s:!:Firefox:2.x
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip,deflate],Accept-Charset=[utf-8;q=0.7,*;q=0.7],Keep-Alive=[300],Connection=[keep-alive]::Firefox/

label = s:!:Firefox:3.x
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip,deflate],Accept-Charset=[utf-8;q=0.7,*;q=0.7],Keep-Alive=[115],Connection=[keep-alive],?Referer::Firefox/

label = s:!:Firefox:4.x
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip, deflate],Accept-Charset=[utf-8;q=0.7,*;q=0.7],Keep-Alive=[115],Connection=[keep-alive],?Referer::Firefox/

; I have no idea where this 'UTF-8' variant comes from, but it happens on *BSD.
; Likewise, no clue why Referer is in a different place for some users.

label = s:!:Firefox:5.x-9.x
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip, deflate],Accept-Charset=[utf-8;q=0.7,*;q=0.7],?DNT=[1],Connection=[keep-alive],?Referer:Keep-Alive:Firefox/
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip, deflate],Accept-Charset=[UTF-8,*],?DNT=[1],Connection=[keep-alive],?Referer:Keep-Alive:Firefox/
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip, deflate],Accept-Charset=[UTF-8,*],?DNT=[1],?Referer,Connection=[keep-alive]:Keep-Alive:Firefox/
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip, deflate],Accept-Charset=[utf-8;q=0.7,*;q=0.7],?DNT=[1],?Referer,Connection=[keep-alive]:Keep-Alive:Firefox/
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language,Accept-Encoding=[gzip, deflate],Accept-Charset=[utf-8;q=0.7,*;q=0.7],?Referer,?DNT=[1],Connection=[keep-alive]:Keep-Alive:Firefox/

label = s:!:Firefox:10.x or newer
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language=[;q=],Accept-Encoding=[gzip, deflate],?DNT=[1],Connection=[keep-alive],?Referer:Accept-Charset,Keep-Alive:Firefox/
sig   = *:Host,User-Agent,Accept=[,*/*;q=],?Accept-Language=[;q=],Accept-Encoding=[gzip, deflate],?DNT=[1],?Referer,Connection=[keep-alive]:Accept-Charset,Keep-Alive:Firefox/

; There is this one weird case where Firefox 10.x is indistinguishable
; from Safari 5.1:

label = s:!:Firefox:10.x or Safari 5.x
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[xml;q=0.9,*/*;q=0.8],Accept-Language,Accept-Encoding=[gzip, deflate],Connection=[keep-alive]:Keep-Alive,Accept-Charset,DNT,Referer:Gecko

; ----
; MSIE
; ----

; MSIE 11 no longer sends the 'MSIE' part in U-A, but we don't consider
; U-A to be a robust signal for fingerprinting, so no dice.

label = s:!:MSIE:8 or newer
sys   = Windows
sig   = 1:Accept=[*/*],?Referer,?Accept-Language,User-Agent,Accept-Encoding=[gzip, deflate],Host,Connection=[Keep-Alive]:Keep-Alive,Accept-Charset,UA-CPU:Trident/
sig   = 1:Accept=[*/*],?Referer,?Accept-Language,Accept-Encoding=[gzip, deflate],User-Agent,Host,Connection=[Keep-Alive]:Keep-Alive,Accept-Charset:(compatible; MSIE

label = s:!:MSIE:7
sys   = Windows
sig   = 1:Accept=[*/*],?Referer,?Accept-Language,UA-CPU,User-Agent,Accept-Encoding=[gzip, deflate],Host,Connection=[Keep-Alive]:Keep-Alive,Accept-Charset:(compatible; MSIE

; TODO: Check if this one ever uses Accept-Language, etc. Also try to find MSIE 5.

label = s:!:MSIE:6
sys   = Windows
sig   = 0:Accept=[*/*],?Referer,User-Agent,Host:Keep-Alive,Connection,Accept-Encoding,Accept-Language,Accept-Charset:(compatible; MSIE
sig   = 1:Accept=[*/*],Connection=[Keep-Alive],Host,?Pragma=[no-cache],?Range,?Referer,User-Agent:Keep-Alive,Accept-Encoding,Accept-Language,Accept-Charset:(compatible; MSIE

; ------
; Chrome
; ------

label = s:!:Chrome:11.x to 26.x
sys   = Windows,@unix
sig   = 1:Host,Connection=[keep-alive],User-Agent,Accept=[*/*],?Referer,Accept-Encoding=[gzip,deflate,sdch],Accept-Language,Accept-Charset=[utf-8;q=0.7,*;q=0.3]:: Chrom
sig   = 1:Host,Connection=[keep-alive],User-Agent,Accept=[*/*],?Referer,Accept-Encoding=[gzip,deflate,sdch],Accept-Language,Accept-Charset=[UTF-8,*;q=0.5]:: Chrom
sig   = 1:Host,User-Agent,Accept=[*/*],?Referer,Accept-Encoding=[gzip,deflate,sdch],Accept-Language,Accept-Charset=[utf-8;q=0.7,*;q=0.3],Connection=[keep-alive]::Chrom

label = s:!:Chrome:27.x to 42.x
sys   = Windows,@unix
sig   = 1:Host,Connection=[keep-alive],Accept=[*/*],User-Agent,?Referer,Accept-Encoding=[gzip,deflate,sdch],Accept-Language:Accept-Charset,Keep-Alive: Chrom

label = s:!:Chrome:43.x or 50.x
sys   = Windows,@unix
sig   = 1:Host,Connection=[keep-alive],Accept=[*/*],User-Agent,?Referer,Accept-Encoding=[gzip, deflate, sdch],Accept-Language:Accept-Charset,Keep-Alive: Chrom

label = s:!:Chrome:51.x or newer
sys   = Windows,@unix
sig   = 1:Host,Connection=[keep-alive],Upgrade-Insecure-Requests=[1],User-Agent,Accept=[*/*],Accept-Encoding=[gzip, deflate, sdch],Accept-Language:Accept-Charset,Keep-Alive: Chrom

; -----
; Opera
; -----

label = s:!:Opera:19.x or newer
sys   = Windows,@unix
sig   = 1:Host,Connection=[keep-alive],Accept=[*/*;q=0.8],User-Agent,Accept-Encoding=[gzip,deflate,lzma,sdch],Accept-Language=[;q=0.]:Accept-Charset,Keep-Alive:OPR/

label = s:!:Opera:15.x-18.x
sys   = Windows,@unix
sig   = 1:Host,Connection=[keep-alive],Accept=[*/*;q=0.8],User-Agent,Accept-Encoding=[gzip, deflate],Accept-Language=[;q=0.]:Accept-Charset,Keep-Alive:OPR/

label = s:!:Opera:11.x-14.x
sys   = Windows,@unix
sig   = 1:User-Agent,Host,Accept=[*/*;q=0.1],?Accept-Language=[;q=0.],Accept-Encoding=[gzip, deflate],Connection=[Keep-Alive]:Accept-Charset,X-OperaMini-Phone-UA:) Presto/

label = s:!:Opera:10.x
sys   = Windows,@unix
sig   = 1:User-Agent,Host,Accept=[*/*;q=0.1],Accept-Language=[;q=0.],Accept-Charset=[utf-8, utf-16, *;q=0.1],Accept-Encoding=[deflate, gzip, x-gzip, identity, *;q=0],Connection=[Keep-Alive]::Presto/
sig   = 1:User-Agent,Host,Accept=[*/*;q=0.1],Accept-Language=[en],Accept-Encoding=[gzip, deflate],Connection=[Keep-Alive]:Accept-Charset:Opera/

label = s:!:Opera:Mini
sys   = Linux
sig   = 1:User-Agent,Host,Accept=[*/*;q=0.1],Accept-Language=[;q=0.],Accept-Encoding=[gzip, deflate],Connection=[Keep-Alive],X-OperaMini-Phone-UA,X-OperaMini-Features,X-OperaMini-Phone,x-forwarded-for:Accept-Charset:Opera Mini/

label = s:!:Opera:on Nintendo Wii
sys   = Nintendo
sig   = 1:User-Agent,Host,Accept=[*/*;q=0.1],Accept-Language=[en],Accept-Charset=[iso-8859-1, utf-8, utf-16, *;q=0.1],Accept-Encoding=[deflate, gzip, x-gzip, identity, *;q=0],Connection=[Keep-Alive]::Nintendo

; ---------------
; Android browser
; ---------------

label = s:!:Android:2.x
sys   = Linux
sig   = 1:Host,Accept-Encoding=[gzip],Accept-Language,User-Agent,Accept=[,*/*;q=0.5],Accept-Charset=[utf-16, *;q=0.7]:Connection:Android
sig   = 1:Host,Connection=[keep-alive],Accept-Encoding=[gzip],Accept-Language,User-Agent,Accept=[,*/*;q=0.5],Accept-Charset=[utf-16, *;q=0.7]::Android
sig   = 1:Host,Accept-Encoding=[gzip],Accept-Language=[en-US],Accept=[*/*;q=0.5],User-Agent,Accept-Charset=[utf-16, *;q=0.7]:Connection:Android

label = s:!:Android:4.x
sys   = Linux
sig   = 1:Host,Connection=[keep-alive],Accept=[,*/*;q=0.8],User-Agent,Accept-Encoding=[gzip,deflate],Accept-Language,Accept-Charset=[utf-16, *;q=0.7]::Android

; ------
; Safari
; ------

label = s:!:Safari:7 or newer
sys   = @unix
sig   = *:Host,Accept-Encoding=[gzip, deflate],Connection=[keep-alive],Accept=[*/*],User-Agent,Accept-Language,?Referer,?DNT:Accept-Charset,Keep-Alive:KHTML, like Gecko)

label = s:!:Safari:5.1-6
sys   = Windows,@unix
sig   = *:Host,User-Agent,Accept=[*/*],?Referer,Accept-Language,Accept-Encoding=[gzip, deflate],Connection=[keep-alive]:Accept-Charset:KHTML, like Gecko)
sig   = *:Host,User-Agent,Accept=[*/*],?Referer,Accept-Encoding=[gzip, deflate],Accept-Language,Connection=[keep-alive]:Accept-Charset:KHTML, like Gecko)

label = s:!:Safari:5.0 or earlier
sys   = Mac OS X
sig   = 0:Host,User-Agent,Connection=[close]:Accept,Accept-Encoding,Accept-Language,Accept-Charset:CFNetwork/

; ---------
; Konqueror
; ---------

label = s:!:Konqueror:4.6 or earlier
sys   = Linux,FreeBSD,OpenBSD
sig   = 1:Host,Connection=[Keep-Alive],User-Agent,?Pragma,?Cache-control,Accept=[*/*],Accept-Encoding=[x-gzip, x-deflate, gzip, deflate],Accept-Charset=[;q=0.5, *;q=0.5],Accept-Language::Konqueror/

label = s:!:Konqueror:4.7 or newer
sys   = Linux,FreeBSD,OpenBSD
sig   = 1:Host,Connection=[keep-alive],User-Agent,Accept=[*/*],Accept-Encoding=[gzip, deflate, x-gzip, x-deflate],Accept-Charset=[,*;q=0.5],Accept-Language::Konqueror/

; -------------------
; Major search robots
; -------------------

label = s:!:BaiduSpider:
sys   = BaiduSpider
sig   = 1:Host,Connection=[close],User-Agent,Accept=[*/*]:Accept-Encoding,Accept-Language,Accept-Charset:Baiduspider-image
sig   = 1:Host,Accept-Language=[zh-cn],Connection=[close],User-Agent:Accept,Accept-Encoding,Accept-Charset:Baiduspider
sig   = 1:Host,Connection=[close],User-Agent,Accept-Language=[zh-cn,zh-tw],Accept-Encoding=[gzip],Accept=[*/*]:Accept-Charset:Baiduspider
sig   = 1:Host,Connection=[close],User-Agent,Accept-Language=[tr-TR],Accept-Encoding=[gzip],Accept=[*/*]:Accept-Charset:Baiduspider
sig   = 1:Host,Connection=[close],User-Agent,Accept-Encoding=[gzip],?Accept-Language=[zh-cn,zh-tw],Accept=[*/*]:Accept-Charset:Baiduspider
sig   = 1:Host,Connection=[close],User-Agent,Accept-Encoding=[gzip],Accept-Language=[tr-TR],Accept=[*/*]:Accept-Charset:Baiduspider

label = s:!:Googlebot:
sys   = Linux
sig   = 1:Host,Connection=[Keep-alive],Accept=[*/*],From=[googlebot(at)googlebot.com],User-Agent,Accept-Encoding=[gzip,deflate],?If-Modified-Since:Accept-Language,Accept-Charset:Googlebot
sig   = 1:Host,Connection=[Keep-alive],Accept=[text/plain],Accept=[text/html],From=[googlebot(at)googlebot.com],User-Agent,Accept-Encoding=[gzip,deflate]:Accept-Language,Accept-Charset:Googlebot

label = s:!:Googlebot:feed fetcher
sys   = Linux
sig   = 1:Host,Connection=[Keep-alive],Accept=[*/*],User-Agent,Accept-Encoding=[gzip,deflate],?If-Modified-Since:Accept-Language,Accept-Charset:-Google
sig   = 1:User-Agent,?X-shindig-dos=[on],Cache-Control,Host,?X-Forwarded-For,Accept-Encoding=[gzip],?Accept-Language:Connection,Accept,Accept-Charset:Feedfetcher-Google

label = s:!:Bingbot:
sys   = Windows
sig   = 1:Cache-Control,Connection=[Keep-Alive],Pragma=[no-cache],Accept=[*/*],Accept-Encoding,Host,User-Agent:Accept-Language,Accept-Charset:bingbot/

; MSNbot has a really silly Accept header, only a tiny part of which is preserved here:

label = s:!:MSNbot:
sys   = Windows
sig   = 1:Connection=[Close],Accept,Accept-Encoding=[gzip, deflate],From=[msnbot(at)microsoft.com],Host,User-Agent:Accept-Language,Accept-Charset:msnbot

label = s:!:Yandex:crawler
sys   = FreeBSD
sig   = 1:Host,Connection=[Keep-Alive],Accept=[*/*],Accept-Encoding=[gzip,deflate],Accept-Language=[en-us, en;q=0.7, *;q=0.01],User-Agent,From=[support@search.yandex.ru]:Accept-Charset:YandexBot/
sig   = 1:Host,Connection=[Keep-Alive],Accept=[image/jpeg, image/pjpeg, image/png, image/gif],User-Agent,From=[support@search.yandex.ru]:Accept-Encoding,Accept-Language,Accept-Charset:YandexImages/
sig   = 1:Host,Connection=[Keep-Alive],User-Agent,From=[support@search.yandex.ru]:Accept,Accept-Encoding,Accept-Language,Accept-Charset:YandexBot/

label = s:!:Yahoo:crawler
sys   = Linux
sig   = 0:Host,User-Agent,Accept=[,image/png,*/*;q=0.5],Accept-Language=[en-us,en;q=0.5],Accept-Encoding=[gzip],Accept-Charset=[,utf-8;q=0.7,*;q=0.7]:Connection:Slurp

; -----------------
; Misc other robots
; -----------------

label = s:!:Flipboard:crawler
sys   = Linux
sig   = 1:User-Agent,Accept-Language=[en-us,en;q=0.5],Accept-Charset=[;q=0.7,*;q=0.5],Accept-Encoding=[gzip],Host,Accept=[*; q=.2, */*; q=.2],Connection=[keep-alive]::FlipboardProxy
sig   = 1:Accept-language=[en-us,en;q=0.5],Accept-encoding=[gzip],Accept=[;q=0.9,*/*;q=0.8],User-agent,Host:User-Agent,Connection,Accept-Encoding,Accept-Language,Accept-Charset:FlipboardProxy

label = s:!:Spinn3r:crawler
sys   = Linux
sig   = 1:User-Agent,Accept-Encoding=[gzip],Host,Accept=[*; q=.2, */*; q=.2],Connection=[close]:Accept-Language,Accept-Charset:Spinn3r

label = s:!:Facebook:crawler
sys   = Linux
sig   = 1:User-Agent,Host,Accept=[*/*],Accept-Encoding=[deflate, gzip],Connection=[close]:Accept-Language,Accept-Charset:facebookexternalhit/
sig   = 1:User-Agent,Host,Accept=[*/*],Connection=[close]:Accept-Encoding,Accept-Language,Accept-Charset:facebookexternalhit/

label = s:!:paper.li:crawler
sys   = Linux
sig   = 1:Accept-Language=[en-us,en;q=0.5],Accept=[*/*],User-Agent,Connection=[close],Accept-Encoding=[gzip,identity],?Referer,Host,Accept-Charset=[ISO-8859-1,utf-8;q=0.7,*;q=0.7]::PaperLiBot/

label = s:!:Twitter:crawler
sys   = Linux
sig   = 1:User-Agent=[Twitterbot/],Host,Accept=[*; q=.2, */*; q=.2],Cache-Control,Connection=[keep-alive]:Accept-Encoding,Accept-Language,Accept-Charset:Twitterbot/

label = s:!:linkdex:crawler
sys   = Linux
sig   = 0:Host,Connection=[Keep-Alive],User-Agent,Accept-Encoding=[gzip,deflate]:Accept,Accept-Language,Accept-Charset:linkdex.com/

label = s:!:Yodaobot:
sys   = Linux
sig   = 1:Accept-Encoding=[identity;q=0.5, *;q=0.1],User-Agent,Host:Connection,Accept,Accept-Language,Accept-Charset:YodaoBot/

label = s:!:Tweetmeme:crawler
sys   = Linux
sig   = 1:Host,User-Agent,Accept=[,image/png,*/*;q=0.5],Accept-Language=[en-gb,en;q=0.5],Accept-Charset=[ISO-8859-1,utf-8;q=0.7,*;q=0.7]:Connection,Accept-Encoding:TweetmemeBot/

label = s:!:Archive.org:crawler
sys   = Linux
sig   = 0:User-Agent,Connection=[close],Accept=[application/xml;q=0.9,*/*;q=0.8],Host:Accept-Encoding,Accept-Language,Accept-Charset:archive.org

label = s:!:Yahoo Pipes:
sys   = Linux
sig   = 0:Client-IP,X-Forwarded-For,X-YQL-Depth,User-Agent,Host,Connection=[keep-alive],Via:Accept,Accept-Encoding,Accept-Language,Accept-Charset:Yahoo Pipes
sig   = 1:Client-IP,X-Forwarded-For,X-YQL-Depth,User-Agent,Host,Via:Connection,Accept,Accept-Encoding,Accept-Language,Accept-Charset:Yahoo Pipes

label = s:!:Google Web Preview:
sys   = Linux
sig   = 1:Referer,User-Agent,Accept-Encoding=[gzip,deflate],Host,X-Forwarded-For:Connection,Accept,Accept-Language,Accept-Charset:Web Preview

; --------------------------------
; Command-line tools and libraries
; --------------------------------

label = s:!:wget:
sys   = @unix,Windows
sig   = *:User-Agent,Accept=[*/*],Host,Connection=[Keep-Alive]:Accept-Encoding,Accept-Language,Accept-Charset:Wget/

label = s:!:Lynx:
sys   = @unix,Windows
sig   = 0:Host,Accept=[text/sgml, */*;q=0.01],Accept-Encoding=[gzip, compress],Accept-Language,User-Agent:Connection,Accept-Charset:Lynx/

label = s:!:curl:
sys   = @unix,Windows
sig   = 1:User-Agent,Host,Accept=[*/*]:Connection,Accept-Encoding,Accept-Language,Accept-Charset:curl/

label = s:!:links:
sys   = @unix,Windows
sig   = 1:Host,User-Agent,Accept=[*/*],Accept-Encoding=[gzip, deflate, bzip2],Accept-Charset=[us-ascii],Accept-Language=[;q=0.1],Connection=[Keep-Alive]::Links
sig   = 1:Host,User-Agent,Accept=[*/*],Accept-Encoding=[gzip,deflate,bzip2],Accept-Charset=[us-ascii],Accept-Language=[;q=0.1],Connection=[keep-alive]::Links

label = s:!:elinks:
sys   = @unix,Windows
sig   = 1:Host,User-Agent,Accept=[*/*],Accept-Encoding=[bzip2, deflate, gzip],Accept-Language:Connection,Accept-Charset:ELinks/

label = s:!:Java:JRE
sys   = @unix,@win
sig   = 1:User-Agent,Host,Accept=[*; q=.2, */*; q=.2],Connection=[keep-alive]:Accept-Encoding,Accept-Language,Accept-Charset:Java/

label = s:!:Python:urllib
sys   = @unix,Windows
sig   = 1:Accept-Encoding=[identity],Host,Connection=[close],User-Agent:Accept,Accept-Language,Accept-Charset:Python-urllib/

label = s:!:w3m:
sys   = @unix,Windows
sig   = 0:User-Agent,Accept=[image/*],Accept-Encoding=[gzip, compress, bzip, bzip2, deflate],Accept-Language=[;q=1.0],Host:Connection,Accept-Charset:w3m/

label = s:!:libfetch:
sys   = @unix
sig   = 1:Host,User-Agent,Connection=[close]:Accept,Accept-Encoding,Accept-Language,Accept-Charset:libfetch/

; -------------
; Odds and ends
; -------------

label = s:!:Google AppEngine:
sys   = Linux
sig   = 1:User-Agent,Host,Accept-Encoding=[gzip]:Connection,Accept,Accept-Language,Accept-Charset:AppEngine-Google

label = s:!:WebOS:
sys   = Linux
sig   = 1:Host,Accept-Encoding=[gzip, deflate],User-Agent,Accept=[,*/*;q=0.5],Accept-Language,Accept-Charset=[utf-8;q=0.7,*;q=0.3]:Connection:wOSBrowser

label = s:!:xxxterm:
sys   = @unix
sig   = 1:Host,User-Agent,Accept=[*/*],Accept-Encoding=[gzip]:Connection,Accept-Language,Accept-Charset:xxxterm

label = s:!:Google Desktop:
sys   = Windows
sig   = 1:Accept=[*/*],Accept-Encoding=[gzip],User-Agent,Host,Connection=[Keep-Alive]:Accept-Language,Accept-Charset:Google Desktop/

label = s:!:luakit:
sys   = @unix
sig   = 1:Host,User-Agent,Accept=[*/*],Accept-Encoding=[gzip],Connection=[Keep-Alive]:Accept-Language,Accept-Charset:luakit

label = s:!:Epiphany:
sys   = @unix
sig   = 1:Host,User-Agent,Accept=[*/*],Accept-Encoding=[gzip],Accept-Language:Connection,Accept-Charset,Keep-Alive:Epiphany/

; ======================
; HTTP server signatures
; ======================

[http:response]

; ------
; Apache
; ------

label = s:!:Apache:2.x
sys   = @unix,Windows
sig   = 1:Date,Server,?Last-Modified,?Accept-Ranges=[bytes],?Content-Length,?Content-Range,Keep-Alive=[timeout],Connection=[Keep-Alive],?Transfer-Encoding=[chunked],Content-Type::Apache
sig   = 1:Date,Server,?Last-Modified,?Accept-Ranges=[bytes],?Content-Length,?Connection=[close],?Transfer-Encoding=[chunked],Content-Type:Keep-Alive:Apache
sig   = 1:Date,Server,Connection=[Keep-Alive],Keep-Alive=[timeout]:Content-Type,Accept-Ranges:Apache
sig   = 1:Date,Server,?Last-Modified,?Accept-Ranges=[bytes],?Content-Length,Content-Type,Keep-Alive=[timeout],Connection=[Keep-Alive]::Apache

label = s:!:Apache:1.x
sys   = @unix,Windows
sig   = 1:Server,Content-Type,?Content-Length,Date,Connection=[keep-alive]:Keep-Alive,Accept-Ranges:Apache
sig   = 1:Server,Content-Type,?Content-Length,Date,Connection=[close]:Keep-Alive,Accept-Ranges:Apache

; ---
; IIS
; ---

label = s:!:IIS:7.x
sys   = Windows
sig   = 1:?Content-Length,Content-Type,?Etag,Server,Date:Connection,Keep-Alive,Accept-Ranges:Microsoft-IIS/
sig   = 1:?Content-Length,Content-Type,?Etag,Server,Date,Connection=[close]:Keep-Alive,Accept-Ranges:Microsoft-IIS/

; --------
; lighttpd
; --------

label = s:!:lighttpd:2.x
sys   = @unix
sig   = 1:?ETag,?Last-Modified,Accept-Ranges=[bytes],Content-Type,?Vary,?Content-Length,Date,Server:Connection,Keep-Alive:lighttpd/
sig   = 1:?ETag,?Last-Modified,Transfer-Encoding=[chunked],Content-Type,?Vary,?Content-Length,Date,Server:Connection,Keep-Alive:lighttpd/

label = s:!:lighttpd:1.x
sys   = @unix
sig   = 1:Content-Type,Accept-Ranges=[bytes],?ETag,?Last-Modified,Date,Server:Connection,Keep-Alive:lighttpd/
sig   = 1:Content-Type,Transfer-Encoding=[chunked],?ETag,?Last-Modified,Date,Server:Connection,Keep-Alive:lighttpd/
sig   = 0:Content-Type,Content-Length,Connection=[close],Date,Server:Keep-Alive,Accept-Ranges:lighttpd/

; -----
; nginx
; -----

label = s:!:nginx:1.x
sys   = @unix
sig   = 1:Server,Date,Content-Type,?Content-Length,?Last-Modified,Connection=[keep-alive],Keep-Alive=[timeout],Accept-Ranges=[bytes]::nginx/
sig   = 1:Server,Date,Content-Type,?Content-Length,?Last-Modified,Connection=[close]:Keep-Alive,Accept-Ranges:nginx/

label = s:!:nginx:0.x
sys   = @unix
sig   = 1:Server,Date,Content-Type,?Content-Length,Connection=[keep-alive],?Last-Modified:Keep-Alive,Accept-Ranges:nginx/
sig   = 1:Server,Date,Content-Type,?Content-Length,Connection=[close],?Last-Modified:Keep-Alive,Accept-Ranges:nginx/

; -------------
; Odds and ends
; -------------

label = s:!:Google Web Server:
sys   = Linux
sig   = *:Content-Type,X-Content-Type-Options=[nosniff],Date,Server=[sffe]:Connection,Accept-Ranges,Keep-Alive,Connection:
sig   = *:Date,Content-Type,Server=[gws]:Connection,Accept-Ranges,Keep-Alive:
sig   = *:Content-Type,X-Content-Type-Options=[nosniff],Server=[GSE]:Connection,Accept-Ranges,Keep-Alive:
