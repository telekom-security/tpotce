FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Setup apk and redis
RUN apk -U --no-cache add redis shadow && \
    cp /root/dist/redis.conf /etc && \
#
# Setup user and group
    groupmod -g 2000 redis && \
    usermod -u 2000 redis && \
#
# Clean up
    apk del --purge \ 
            shadow && \
    rm -rf /root/* && \
    rm -rf /tmp/* /var/tmp/* && \
    rm -rf /var/cache/apk/*
#
# Start redis
STOPSIGNAL SIGKILL
USER redis:redis
CMD redis-server /etc/redis.conf
