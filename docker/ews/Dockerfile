FROM alpine:latest
#
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
            build-base \
            git \
            libffi-dev \
            libssl1.1 \
            openssl-dev \
	    python3 \
            python3-dev \
            py3-cffi \
            py3-ipaddress \
	    py3-lxml \
	    py3-mysqlclient \
            py3-requests \
	    py3-pip \
            py3-setuptools && \
    pip3 install --no-cache-dir configparser hpfeeds3 pyOpenSSL xmljson && \
#
# Setup ewsposter
    git clone --depth=1 https://github.com/dtag-dev-sec/ewsposter /opt/ewsposter && \
    mkdir -p /opt/ewsposter/spool /opt/ewsposter/log && \
#
# Setup user and groups
    addgroup -g 2000 ews && \
    adduser -S -H -u 2000 -D -g 2000 ews && \
    chown -R ews:ews /opt/ewsposter && \
#
# Supply configs
    mv /root/dist/ews.cfg /opt/ewsposter/ && \
#    mv /root/dist/*.pem /opt/ewsposter/ && \
#
# Clean up
    apk del build-base \
            git \
            openssl-dev \
            python3-dev \
            py-setuptools && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Run ewsposter
STOPSIGNAL SIGINT
USER ews:ews
CMD sleep 10 && exec /usr/bin/python3 -u /opt/ewsposter/ews.py -l $(shuf -i 10-60 -n 1)
