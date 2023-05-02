FROM alpine:3.17
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
    git clone https://github.com/telekom-security/ewsposter -b v1.25.0 /opt/ewsposter && \
    mkdir -p /opt/ewsposter/spool /opt/ewsposter/log && \
#
# Setup user and groups
    addgroup -g 2000 ews && \
    adduser -S -H -u 2000 -D -g 2000 ews && \
    chown -R ews:ews /opt/ewsposter && \
#
# Supply config and entrypoint.sh
    mv /root/dist/ews.cfg /opt/ewsposter/ && \
    mv /root/dist/entrypoint.sh /usr/bin/ && \
    chmod 755 /usr/bin/entrypoint.sh && \
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
CMD /usr/bin/entrypoint.sh
