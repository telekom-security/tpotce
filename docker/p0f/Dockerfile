FROM alpine:3.24
#
# Add source
COPY . /opt/p0f
COPY --chmod=0755 dist/capture-if.sh /usr/bin/capture-if.sh
COPY --chmod=0755 dist/run.sh /usr/bin/p0f-run.sh
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		bash \
		build-base \
		jansson \
		jansson-dev \
		libcap \
		libpcap \
		libpcap-dev && \
#
# Setup user, groups and configs
    addgroup -g 2000 p0f && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 p0f && \
#
# Download and compile p0f
    cd /opt/p0f && \
    ./build.sh && \
    setcap cap_sys_chroot,cap_setgid,cap_net_raw=+ep /opt/p0f/p0f && \
#
# Clean up
    apk del --purge build-base \
                    jansson-dev \
                    libpcap-dev && \
    rm -rf /root/* \
           /var/cache/apk/*
#
# Start p0f: live capture, or with arguments offline (pcap to JSON on stdout)
WORKDIR /opt/p0f
USER p0f:p0f
ENTRYPOINT ["/usr/bin/p0f-run.sh"]
