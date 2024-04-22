FROM alpine:edge
#
# Include dist
COPY dist/ /opt/tpot/
#
# Get and install dependencies & packages
RUN apk --no-cache -U add \
        aria2 \
        apache2-utils \
        bash \
        bind-tools \
        conntrack-tools \
		cracklib \
        curl \
        ethtool \
        figlet \
        git \
        grep \
        iproute2 \
        iptables \
#        iptables-legacy \
        jq \
        logrotate \
        lsblk \
        net-tools \
        openssl \
        pigz \
        tar \
        uuidgen && \
    apk --no-cache -U add --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
        yq && \
#
# Setup user
    addgroup -g 2000 tpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 tpot && \
#
# Clean up
    apk del --purge git && \
    rm -rf /root/* /tmp/* && \
    rm -rf /root/.cache /opt/tpot/.git && \
    rm -rf /var/cache/apk/*
#
# Run tpotinit
WORKDIR /opt/tpot
#HEALTHCHECK --interval=5s --timeout=30s --retries=3 CMD pgrep -f autoheal || exit 1
HEALTHCHECK --retries=1000 --interval=5s CMD test -f /tmp/success || exit 1
STOPSIGNAL SIGTERM
# Using ENTRYPOINT so we can catch SIGTERM for cleanup
ENTRYPOINT ["/opt/tpot/entrypoint.sh"]
