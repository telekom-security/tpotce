FROM alpine

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
                 ca-certificates \
                 curl \
                 file \
                 geoip \
                 hiredis \
                 jansson \
                 libcap-ng \
                 libhtp \
                 libmagic \
                 libnet \
                 libnetfilter_queue \
                 libnfnetlink \
                 libpcap \
                 luajit \
                 lz4-libs \
                 musl \
                 nspr \
                 nss \
                 pcre \
                 yaml \
                 wget \
                 automake \
                 autoconf \
                 build-base \
                 cargo \
                 file-dev \
                 geoip-dev \
                 hiredis-dev \
                 jansson-dev \
                 libtool \
                 libhtp-dev \
                 libcap-ng-dev \
                 luajit-dev \
                 libpcap-dev \
                 libnet-dev \
                 libnetfilter_queue-dev \
                 libnfnetlink-dev \
                 lz4-dev \
                 nss-dev \
                 nspr-dev \
                 pcre-dev \
                 python2 \
                 py2-pip \
                 rust \
                 yaml-dev && \

# Upgrade pip, install virtualenv
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir suricata-update && \

# Get and build Suricata
    mkdir -p /opt/builder/ && \
    wget https://www.openinfosecfoundation.org/download/suricata-4.1.3.tar.gz && \
    tar xvfz suricata-4.1.3.tar.gz --strip-components=1 -C /opt/builder/ && \
    rm suricata-4.1.3.tar.gz && \
    cd /opt/builder && \
    ./configure \
	--prefix=/usr \
	--sysconfdir=/etc \
	--mandir=/usr/share/man \
	--localstatedir=/var \
	--enable-non-bundled-htp \
	--enable-nfqueue \
        --enable-rust \
	--disable-gccmarch-native \
	--enable-hiredis \
	--enable-geoip \
	--enable-gccprotect \
	--enable-pie \
	--enable-luajit && \
    make && \
    make check && \
    make install && \
    make install-full && \

# Setup user, groups and configs
    addgroup -g 2000 suri && \
    adduser -S -H -u 2000 -D -g 2000 suri && \
    chmod 644 /etc/suricata/*.config && \
    cp /root/dist/suricata.yaml /etc/suricata/suricata.yaml && \
    cp /root/dist/*.bpf /etc/suricata/ && \
    mkdir -p /etc/suricata/rules && \
    cp /opt/builder/rules/* /etc/suricata/rules/ && \

# Download the latest EmergingThreats ruleset, replace rulebase and enable all rules
    cp /root/dist/update.sh /usr/bin/ && \
    chmod 755 /usr/bin/update.sh && \
    update.sh OPEN && \

# Clean up
    apk del --purge \
                 automake \
                 autoconf \
                 build-base \
                 cargo \
                 file-dev \
                 geoip-dev \
                 hiredis-dev \
                 jansson-dev \
                 libtool \
                 libhtp-dev \
                 libcap-ng-dev \
                 luajit-dev \
                 libpcap-dev \
                 libnet-dev \
                 libnetfilter_queue-dev \
                 libnfnetlink-dev \
                 lz4-dev \
                 nss-dev \
                 nspr-dev \
                 pcre-dev \
                 python2 \
                 py2-pip \
                 rust \
                 yaml-dev && \
    rm -rf /opt/builder && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start suricata
STOPSIGNAL SIGINT
CMD SURICATA_CAPTURE_FILTER=$(update.sh $OINKCODE) && exec suricata -v -F $SURICATA_CAPTURE_FILTER -i $(/sbin/ip address | grep '^2: ' | awk '{ print $2 }' | tr -d [:punct:])
