FROM alpine
MAINTAINER MO

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
               libxslt \
               libxslt-dev \
               mariadb-dev \
               mariadb-client-libs \
               pkgconfig \
               python \
               python-dev \
               py-cffi \
               py-cryptography \
               tcpdump \
               wget && \

# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot/ && \
    cd /opt/conpot/ && \
    cp /root/dist/requirements.txt /opt/conpot/ && \
    python setup.py install && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \

# Get wireshark manuf db for scapy, setup configs, user, groups
    mkdir -p /etc/conpot /var/log/conpot /usr/share/wireshark && \
    wget https://github.com/wireshark/wireshark/raw/master/manuf -o /usr/share/wireshark/manuf && \
    mv /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    mv /root/dist/kamstrup_382/template.xml /usr/lib/python2.7/site-packages/Conpot-0.5.1-py2.7.egg/conpot/templates/kamstrup_382/ && \
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

# Run supervisor upon container start
CMD ["/usr/bin/conpot", "--template", "kamstrup_382", "--logfile", "/var/log/conpot/conpot.log", "--config", "/etc/conpot/conpot.cfg"]
