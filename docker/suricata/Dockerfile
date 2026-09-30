FROM alpine:3.24
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		ca-certificates \
		curl \
		file \
		hiredis \
		libcap \
		wget \
		suricata && \
#
# Setup user, groups and configs
    addgroup -g 2000 suri && \
    adduser -S -H -u 2000 -D -g 2000 suri && \
    cp /root/dist/*.yaml /etc/suricata/ && \
    cp /root/dist/*.conf /etc/suricata/ && \
    cp /root/dist/*.bpf /etc/suricata/ && \
    cp /root/dist/entrypoint.sh /root/dist/capture-if.sh /usr/bin/ && \
    chmod 644 /etc/suricata/*.config && \
    chmod 755 -R /var/lib/suricata && \
    chmod 755 /usr/bin/entrypoint.sh /usr/bin/capture-if.sh && \
    chown -R root:suri /tmp /run && \
#
# Download the latest EmergingThreats OPEN ruleset, kept as the last resort for
# the first start without internet access (the entrypoint caches newer ones)
    suricata-update update-sources && \
    suricata-update --no-test --no-reload && \
    mkdir -p /var/lib/suricata/image && \
    cp /var/lib/suricata/rules/suricata.rules /var/lib/suricata/image/suricata.rules && \
    rm -rf /var/lib/suricata/tpot && \
#
# Clean up
    rm -rf /root/* \
           /tmp/* \
           /var/cache/apk/*
#
# Start suricata
STOPSIGNAL SIGINT
ENTRYPOINT ["entrypoint.sh"]
