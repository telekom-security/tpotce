#
# p0f - Makefile for tools
# ------------------------
#
# Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>
#
# Distributed under the terms and conditions of GNU LGPL.
#

CC      = gcc
CFLAGS  = -g -ggdb -Wall -Wno-format -funsigned-char
LDFLAGS =
TARGETS = p0f-client p0f-sendsyn p0f-sendsyn6

all: $(TARGETS)

clean:
	rm -f -- $(TARGETS) *.exe *.o a.out *~ core core.[1-9][0-9]* *.stackdump 2>/dev/null
