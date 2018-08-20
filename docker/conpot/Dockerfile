FROM alpine:3.7

# Include dist
ADD dist/ /root/dist/

# Setup apt
RUN apk -U --no-cache add \
               build-base \
               cython-dev \
               file \
               git \
               libev \
               libtool \
               libcap \
               libxml2-dev \
               libxslt \
               mariadb-client-libs \
               mariadb-dev \
               pkgconfig \
               python \
               python-dev \
               py-cffi \
               py-cryptography \
               py-pip \
               tcpdump \
               wget && \

# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot && \
    cd /opt/conpot/ && \
    git reset --hard d157229e4587188ad3d3af5dddcd71200713852d && \
    git fetch origin pull/367/head:run-without-root && \
    git checkout run-without-root && \
    git checkout master && \
    git merge run-without-root && \
    cp /root/dist/requirements.txt /opt/conpot/ && \
    # Patch to accept ENV for MIB path
    cp /root/dist/snmp_server.py /opt/conpot/conpot/protocols/snmp/ && \
    pip install --no-cache-dir -U pip setuptools && \
    pip install --no-cache-dir . && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep /usr/bin/python2.7 && \
    
# Get wireshark manuf db for scapy, setup configs, user, groups
    mkdir -p /etc/conpot /var/log/conpot /usr/share/wireshark && \
    wget https://github.com/wireshark/wireshark/raw/master/manuf -o /usr/share/wireshark/manuf && \
    cp /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    cp -R /root/dist/templates /usr/lib/python2.7/site-packages/conpot/ && \
    addgroup -g 2000 conpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 conpot && \

# Clean up
    apk del --purge \
            build-base \
            cython-dev \
            file \
            git \
            libev \
            libtool \
            libxml2-dev \
            libxslt-dev \
            mariadb-dev \
            pkgconfig \
            python-dev \
            py-cffi \
            py-pip \
            wget && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start conpot
USER conpot:conpot
CMD exec /usr/bin/conpot --template $CONPOT_TEMPLATE --logfile $CONPOT_LOG --config $CONPOT_CONFIG
