FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Setup apt
RUN apk --no-cache -U add \
             build-base \
	     cython \
             file \
             git \
             libev \
             libtool \
             libcap \
	     libffi-dev \
             libxslt \
             libxslt-dev \
             mariadb-dev \
             pkgconfig \
	     procps \
             python3 \
             python3-dev \
	     py3-cffi \
	     py3-cryptography \
	     py3-freezegun \
	     py3-gevent \
	     py3-lxml \
	     py3-natsort \
	     py3-pip \
	     py3-ply \
	     py3-psutil \
	     py3-pycryptodomex \
	     py3-pytest \
	     py3-requests \
             py3-pyserial \
	     py3-setuptools \
	     py3-slugify \
	     py3-snmp \
	     py3-sphinx \
	     py3-wheel \
	     py3-zope-event \
	     py3-zope-interface \
             wget && \
#
# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot && \
    cd /opt/conpot/ && \
    git checkout b3740505fd26d82473c0d7be405b372fa0f82575 && \
    #git checkout 1c2382ea290b611fdc6a0a5f9572c7504bcb616e && \
    # Change template default ports if <1024
    sed -i 's/port="2121"/port="21"/' /opt/conpot/conpot/templates/default/ftp/ftp.xml && \
    sed -i 's/port="8800"/port="80"/' /opt/conpot/conpot/templates/default/http/http.xml && \
    sed -i 's/port="6230"/port="623"/' /opt/conpot/conpot/templates/default/ipmi/ipmi.xml && \
    sed -i 's/port="5020"/port="502"/' /opt/conpot/conpot/templates/default/modbus/modbus.xml && \
    sed -i 's/port="10201"/port="102"/' /opt/conpot/conpot/templates/default/s7comm/s7comm.xml && \
    sed -i 's/port="16100"/port="161"/' /opt/conpot/conpot/templates/default/snmp/snmp.xml && \
    sed -i 's/port="6969"/port="69"/' /opt/conpot/conpot/templates/default/tftp/tftp.xml && \
    sed -i 's/port="16100"/port="161"/' /opt/conpot/conpot/templates/IEC104/snmp/snmp.xml && \
    sed -i 's/port="6230"/port="623"/' /opt/conpot/conpot/templates/ipmi/ipmi/ipmi.xml && \
    cp /root/dist/requirements.txt . && \
    pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir . && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.10 && \
#
# Get wireshark manuf db for scapy, setup configs, user, groups
    mkdir -p /etc/conpot /var/log/conpot /usr/share/wireshark && \
    wget https://github.com/wireshark/wireshark/raw/master/manuf -o /usr/share/wireshark/manuf && \
    cp /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    cp -R /root/dist/templates /usr/lib/python3.10/site-packages/conpot/ && \
    addgroup -g 2000 conpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 conpot && \
#
# Clean up
    apk del --purge \
            build-base \
            file \
            git \
            libev \
            libtool \
            libxslt-dev \
            mariadb-dev \
            pkgconfig \
            python3-dev \
            wget && \
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*
#
# Start conpot
STOPSIGNAL SIGINT
# Conpot sometimes hangs at 100% CPU usage, if detected process will be killed and container restarts per docker-compose settings
HEALTHCHECK CMD if [ $(ps -C mpv -p 1 -o %cpu | tail -n 1 | cut -f 1 -d ".") -gt 75 ]; then kill -2 1; else exit 0; fi
USER conpot:conpot
CMD exec /usr/bin/conpot --mibcache $CONPOT_TMP --temp_dir $CONPOT_TMP --template $CONPOT_TEMPLATE --logfile $CONPOT_LOG --config $CONPOT_CONFIG
