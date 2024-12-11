FROM alpine:3.20
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		build-base \
		git \
		libcap \
		py3-pip \
		python3 \
		python3-dev \
		tzdata && \
#	     
# Install from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/t-pot-attack-map -b 2.2.6 && \
    cd t-pot-attack-map && \
    pip3 install --break-system-packages --upgrade pip && \
    pip3 install --break-system-packages -r requirements.txt && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Setup user, groups and configs
    addgroup -g 2000 map && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 map && \
    chown map:map -R /opt/t-pot-attack-map && \
#
# Clean up
    apk del --purge build-base \
		git \
		python3-dev && \
    rm -rf /root/* /var/cache/apk/* \
           /opt/t-pot-attack-map/.git
#
# Start T-Pot-Attack-Map
ENV TZ=UTC
STOPSIGNAL SIGINT
USER map:map
WORKDIR /opt/t-pot-attack-map
CMD ["/bin/sh", "-c", "/usr/bin/python3 $MAP_COMMAND"]
