FROM alpine:edge
#
# Install packages
RUN apk -U add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing \
            sentrypeer && \
#
# Setup user, groups and configs
    mkdir -p /var/log/sentrypeer && \
    addgroup -g 2000 sentrypeer && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 sentrypeer && \
    chown -R sentrypeer:sentrypeer /usr/bin/sentrypeer && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start sentrypeer
STOPSIGNAL SIGKILL
USER sentrypeer:sentrypeer
CMD /usr/bin/sentrypeer -jar -f /var/log/sentrypeer/sentrypeer.db -l /var/log/sentrypeer/sentrypeer.json
