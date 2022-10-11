FROM alpine:3.16
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
            build-base \
            git \
            libffi-dev \
            libssl1.1 \
            openssl-dev \
	    python3 \
            python3-dev \
            py3-cffi \
	    py3-cryptography \
            py3-ipaddress \
	    py3-lxml \
	    py3-mysqlclient \
	    py3-openssl \
            py3-requests \
	    py3-pip \
            py3-setuptools \
            py3-wheel && \
    pip3 install --upgrade pip && \
    pip3 install --no-cache-dir configparser hpfeeds3 influxdb influxdb-client xmljson && \
#
# Setup ewsposter
    git clone https://github.com/telekom-security/ewsposter /opt/ewsposter && \
    cd /opt/ewsposter && \
#    git checkout 17c08f3ae500d838c1528c9700e4430d5f6ad214 && \
    git checkout e625aeca432d2df51ef8697363816dfaf4d39886 && \
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
    rm -rf /root/* /var/cache/apk/* /opt/ewsposter/.git
#
# Run ewsposter
STOPSIGNAL SIGINT
USER ews:ews
CMD sleep 10 && exec /usr/bin/python3 -u /opt/ewsposter/ews.py -l $(shuf -i 10-60 -n 1)
