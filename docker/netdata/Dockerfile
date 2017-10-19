FROM alpine
MAINTAINER MO

# Install packages
RUN apk -U upgrade && \
    apk add alpine-sdk autoconf automake bash curl gawk gcc iw jq libmnl-dev libuuid linux-headers lm_sensors make musl-dev netcat-openbsd util-linux-dev pkgconf python py-requests py-yaml zlib-dev && \

# Install netdata
    cd /root && \
    git clone https://github.com/firehol/netdata && \
    cd netdata && \
    ./netdata-installer.sh --dont-wait --dont-start-it && \
    sed -i "s/#local:/local:/" /etc/netdata/python.d/elasticsearch.conf && \
    sed -i "s/# host: '127.0.0.1'/host: '127.0.0.1'/" /etc/netdata/python.d/elasticsearch.conf && \
    sed -i "s/port: '9200'/port: '64298'/" /etc/netdata/python.d/elasticsearch.conf && \
    sed -i "s/# cluster_health: True/cluster_health: True/" /etc/netdata/python.d/elasticsearch.conf && \
    sed -i "s/# cluster_stats: True/cluster_stats: True/" /etc/netdata/python.d/elasticsearch.conf && \
    sed -i 's/SEND_EMAIL="YES"/SEND_EMAIL="NO"/' /etc/netdata/health_alarm_notify.conf && \
    echo 0 > /usr/share/netdata/web/version.txt && \
    cd / && \

# Clean up
    apk del alpine-sdk autoconf automake gcc libmnl-dev linux-headers make musl-dev pkgconf util-linux-dev zlib-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:64301'

# Start netdata 
WORKDIR /
CMD ["/usr/sbin/netdata","-D","-s","/host","-i","127.0.0.1","-p","64301"]
