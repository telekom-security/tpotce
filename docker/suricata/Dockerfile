FROM alpine:edge
#
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
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
    chmod 644 /etc/suricata/*.config && \
    cp /root/dist/*.yaml /etc/suricata/ && \
    cp /root/dist/*.conf /etc/suricata/ && \
    cp /root/dist/*.bpf /etc/suricata/ && \
#
# Download the latest EmergingThreats OPEN ruleset
    cp /root/dist/update.sh /usr/bin/ && \
    chmod 755 /usr/bin/update.sh && \
    suricata-update update-sources && \
    suricata-update --no-reload && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /tmp/* && \
    rm -rf /var/cache/apk/*
#
# Start suricata
STOPSIGNAL SIGINT
CMD SURICATA_CAPTURE_FILTER=$(update.sh $OINKCODE) && exec suricata -v -F $SURICATA_CAPTURE_FILTER -i $(/sbin/ip address show | /usr/bin/awk '/inet.*brd/{ print $NF; exit }')
