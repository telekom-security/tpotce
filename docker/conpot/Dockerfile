FROM alpine

# Include dist
ADD dist/ /root/dist/

# Setup apt
RUN apk -U --no-cache add bash \
               build-base \
               cython-dev \
               file \
               git \
               libev \
               libtool \
               libcap \
               libxslt \
               libxslt-dev \
               mariadb-dev \
               mariadb-client-libs \
               pkgconfig \
               python \
               python-dev \
               py-cffi \
               py-cryptography \
               py-pip \
               tcpdump \
               wget && \

# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot/ && \
    cd /opt/conpot/ && \
    git checkout d157229e4587188ad3d3af5dddcd71200713852d && \
    cp /root/dist/requirements.txt /opt/conpot/ && \
    # Patch to accept ENV for MIB path
    cp /root/dist/snmp_server.py /opt/conpot/conpot/protocols/snmp/ && \
    pip install -U pip setuptools && \
    python setup.py install && \
    cd / && \
    # Monkey patch, see https://github.com/mushorg/conpot/issues/361
    cp /root/dist/conpot.bin /usr/bin/conpot && \
    chmod u+x /usr/bin/conpot && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep /usr/bin/conpot && \
    
# Get wireshark manuf db for scapy, setup configs, user, groups
    mkdir -p /etc/conpot /var/log/conpot /usr/share/wireshark && \
    wget https://github.com/wireshark/wireshark/raw/master/manuf -o /usr/share/wireshark/manuf && \
    cp /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    cp -R /root/dist/templates /usr/lib/python2.7/site-packages/Conpot-0.5.1-py2.7.egg/conpot/ && \
    addgroup -g 2000 conpot && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 conpot && \

# Clean up
    apk del --purge build-base \
            cython-dev \
            file \
            git \
            libev \
            libtool \
            libxslt-dev \
            mariadb-dev \
            pkgconfig \
            python-dev \
            py-cffi \
            wget && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start conpot
CMD exec /usr/bin/conpot --template $CONPOT_TEMPLATE --logfile $CONPOT_LOG --config $CONPOT_CONFIG
