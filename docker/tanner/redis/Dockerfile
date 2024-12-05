FROM alpine:3.20
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add redis shadow && \
    cp /root/dist/redis.conf /etc && \
#
# Setup user and group
    groupmod -g 2000 redis && \
    usermod -u 2000 redis && \
#
# Clean up
    apk del --purge \ 
            shadow && \
    rm -rf /root/* \
           /tmp/* \
           /var/tmp/* \
           /var/cache/apk/*
#
# Start redis
STOPSIGNAL SIGKILL
USER redis:redis
CMD ["redis-server", "/etc/redis.conf"]
