FROM alpine:3.24
#
# Upstream commit; dist/patches/*.patch are applied on top (one file per open
# upstream PR). Once a PR is merged, bump the commit and delete its patch file.
ARG EWSPOSTER_COMMIT=c6b44c7544e71a637d1bcc019abf4a248afb05d4
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
	apk --no-cache -U add \
		build-base \
		git \
		libffi-dev \
		libssl3 \
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
		py3-wheel \
		py3-xmltodict && \
	pip3 install --break-system-packages --upgrade pip && \
	pip3 install --break-system-packages --no-cache-dir \
			configparser \
			hpfeeds3 \
			influxdb \
			influxdb-client \
			xmljson && \
#
# Setup ewsposter
    git clone https://github.com/telekom-security/ewsposter /opt/ewsposter && \
    cd /opt/ewsposter && \
    git checkout ${EWSPOSTER_COMMIT} && \
    for p in /root/dist/patches/*.patch; do [ -e "$p" ] || continue; git apply -v "$p" || exit 1; done && \
    cd / && \
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
