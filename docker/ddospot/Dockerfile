FROM alpine:3.20 AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		build-base \
		git \
		py3-colorama \
		py3-greenlet \
		py3-pip \
		py3-schedule \
		py3-sqlalchemy \
		py3-twisted \
		py3-wheel \
		python3 \
		python3-dev && \
#	     
# Install ddospot from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/ddospot && \
    cd ddospot && \
    git checkout 66b94f3cf56c66e2e26b55feff9e65493cfadf3c && \
    cp /root/dist/requirements.txt . && \
    pip3 install --break-system-packages -r ddospot/requirements.txt && \
    pip3 install --break-system-packages pyinstaller
WORKDIR /opt/ddospot/ddospot
RUN pyinstaller ddospot.py \
       --add-data "core:core" \
       --hidden-import core \
       --hidden-import cmd \
       --hidden-import configparser \
       --hidden-import colorama \
       --hidden-import tabulate \
       --hidden-import logging.handlers \
       --hidden-import hpfeeds \
       --hidden-import json \
       --hidden-import http.server \
       --hidden-import sys \
       --hidden-import sqlalchemy \
       --hidden-import sqlalchemy.ext.declarative \
       --hidden-import schedule \
       --hidden-import twisted \
       --hidden-import twisted.internet \
       --hidden-import twisted.internet.reactor \
       --hidden-import twisted.names.client \
       --hidden-import twisted.names.server \
       --hidden-import twisted.python \
       --hidden-import OpenSSL.crypto \
       --hidden-import OpenSSL.SSL
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/ddospot/ddospot/dist/ddospot/ /opt/ddospot/ddospot
COPY --from=builder /opt/ddospot/ddospot/global.conf /opt/ddospot/ddospot/
COPY --from=builder /opt/ddospot/ddospot/pots /opt/ddospot/ddospot/pots
#
# Start ddospot
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/ddospot/ddospot
CMD ["./ddospot", "-n"]
