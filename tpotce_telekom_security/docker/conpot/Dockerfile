FROM alpine:3.19
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
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
    git clone https://github.com/t3chn0m4g3/cpppo /opt/cpppo && \
    cd /opt/cpppo && \
    git checkout 350d5187a941e7359c53087dcb1f0e41ece5682c && \
    pip3 install --break-system-packages --no-cache-dir --upgrade pip && \
    pip3 install --break-system-packages --no-cache-dir . && \
    git clone https://github.com/mushorg/conpot /opt/conpot && \
    cd /opt/conpot/ && \
	git checkout 26c67d11b08a855a28e87abd186d959741f46c7f && \
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
    pip3 install --break-system-packages --no-cache-dir . && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Get wireshark manuf db for scapy, setup configs, user, groups
    mkdir -p /etc/conpot /var/log/conpot /usr/share/wireshark && \
    wget https://www.wireshark.org/download/automated/data/manuf -o /usr/share/wireshark/manuf && \
    cp /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    cp -R /root/dist/templates /usr/lib/$(readlink -f $(type -P python3) | cut -f4 -d"/")/site-packages/conpot/ && \
    cp /root/dist/cpu_check.py / && \
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
            py3-pip \
            python3-dev \
            wget && \
    rm -rf /root/* \
           /tmp/* \
           /var/cache/apk/* \
           /opt/cpppo/.git \
           /opt/conpot/.git
#
# Start conpot
STOPSIGNAL SIGINT
# Conpot sometimes hangs at 100% CPU usage, if detected container will become unhealthy and restarted by tpotinit
HEALTHCHECK --interval=5m --timeout=30s --retries=3 CMD python3 /cpu_check.py $(pgrep -of conpot) 99
USER conpot:conpot
CMD exec /usr/bin/conpot --mibcache $CONPOT_TMP --temp_dir $CONPOT_TMP --template $CONPOT_TEMPLATE --logfile $CONPOT_LOG --config $CONPOT_CONFIG
