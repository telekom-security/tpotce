FROM alpine:edge
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U upgrade --no-cache && \
	apk -U add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing \
		sentrypeer && \
#
# Setup user, groups and configs
    mkdir -p /var/log/sentrypeer && \
    addgroup -g 2000 sentrypeer && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 sentrypeer && \
    mkdir -p /home/sentrypeer/.config/sentrypeer && \
    cp /root/dist/default-config.toml /home/sentrypeer/.config/sentrypeer && \
    chown -R sentrypeer:sentrypeer /usr/bin/sentrypeer && \
#
# Clean up
    rm -rf /root/* \
           /var/cache/apk/*
#
# Set workdir and start sentrypeer
STOPSIGNAL SIGKILL
USER sentrypeer:sentrypeer
WORKDIR /var/log/sentrypeer/
CMD /usr/bin/sentrypeer -jar -f /var/log/sentrypeer/sentrypeer.db -l /var/log/sentrypeer/sentrypeer.json
