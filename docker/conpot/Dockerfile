FROM alpine:edge
#
# Include dist
ADD dist/ /root/dist/
#
# Setup apt
#RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
RUN apk -U add \
             build-base \
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
	     py3-pip \
             python3 \
             python3-dev \
             py-cffi \
             py-cryptography \
             tcpdump \
             wget && \
#
# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot && \
    cd /opt/conpot/ && \
    git checkout ff09e009d10d953aa7dcff2c06b7c890e6ffd4b7 && \
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
    pip3 install --no-cache-dir -U setuptools && \
    pip3 install --no-cache-dir . && \
    pip3 install --no-cache-dir pysnmp-mibs && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.8 && \
#    
# Get wireshark manuf db for scapy, setup configs, user, groups
    mkdir -p /etc/conpot /var/log/conpot /usr/share/wireshark && \
    wget https://github.com/wireshark/wireshark/raw/master/manuf -o /usr/share/wireshark/manuf && \
    cp /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    cp -R /root/dist/templates /usr/lib/python3.8/site-packages/conpot/ && \
    addgroup -g 2000 conpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 conpot && \
#
# Clean up
    apk del --purge \
            build-base \
            cython-dev \
            file \
            git \
            libev \
            libtool \
            libxslt-dev \
            mariadb-dev \
            pkgconfig \
            python3-dev \
            py-cffi \
            wget && \
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*
#
# Start conpot
STOPSIGNAL SIGINT
USER conpot:conpot
CMD exec /usr/bin/conpot --mibcache $CONPOT_TMP --temp_dir $CONPOT_TMP --template $CONPOT_TEMPLATE --logfile $CONPOT_LOG --config $CONPOT_CONFIG
