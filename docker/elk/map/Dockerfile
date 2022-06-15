FROM alpine:3.15
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
             build-base \
             git \
	     libcap \
	     py3-pip \
             python3 \
             python3-dev && \
#	     
# Install  Server from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/geoip-attack-map && \
    cd geoip-attack-map && \
#    git checkout 4dae740178455f371b667ee095f824cb271f07e8 && \
    cp /root/dist/* . && \
    pip3 install --upgrade pip && \
    pip3 install -r requirements.txt && \
    pip3 install flask && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.9 && \
#
# Setup user, groups and configs
    addgroup -g 2000 map && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 map && \
    chown map:map -R /opt/geoip-attack-map && \
#
# Clean up
    apk del --purge build-base \
                    git \
		    python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/geoip-attack-map/.git
#
# Start wordpot
STOPSIGNAL SIGINT
USER map:map
WORKDIR /opt/geoip-attack-map
CMD ./entrypoint.sh && exec /usr/bin/python3 $MAP_COMMAND
