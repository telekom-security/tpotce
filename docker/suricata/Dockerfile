FROM alpine:edge
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
    cp /root/dist/update.sh /usr/bin/ && \
    chmod 644 /etc/suricata/*.config && \
    chmod 755 -R /var/lib/suricata && \
    chmod 755 /usr/bin/update.sh && \
    chown -R root:suri /tmp /run && \
#
# Download the latest EmergingThreats OPEN ruleset
    suricata-update update-sources && \
    suricata-update --no-test --no-reload && \
#
# Clean up
    rm -rf /root/* \
           /tmp/* \
           /var/cache/apk/*
#
# Start suricata
STOPSIGNAL SIGINT
CMD SURICATA_CAPTURE_FILTER=$(update.sh $OINKCODE) && exec suricata -v -F $SURICATA_CAPTURE_FILTER -i $(ip route | grep "^default" | awk '{ print $5 }')
