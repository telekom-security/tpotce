FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN apk -U upgrade && \
    apk add build-base git libssl1.0 openssl-dev python-dev py-cffi py-ipaddress py-lxml py-mysqldb py-pip py-pysqlite py-requests py-setuptools && \
    pip install pyOpenSSL==16.2.0 && \

# Setup ewsposter
    git clone https://github.com/rep/hpfeeds /opt/hpfeeds && \
    cd /opt/hpfeeds && \
    python setup.py install && \
    git clone https://github.com/vorband/ewsposter /opt/ewsposter && \
    mkdir -p /opt/ewsposter/spool /opt/ewsposter/log && \

# Setup user and groups
    addgroup -g 2000 ews && \
    adduser -S -H -u 2000 -D -g 2000 ews && \

# Supply configs
    mv /root/dist/ews.cfg /opt/ewsposter/ && \

# Clean up
    apk del build-base git openssl-dev python-dev py-pip py-setuptools && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Run ewsposter
CMD sleep 10 && /usr/bin/python /opt/ewsposter/ews.py -l 60
