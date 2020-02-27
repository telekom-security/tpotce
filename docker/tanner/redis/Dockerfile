FROM redis:alpine
#
# Include dist
ADD dist/ /root/dist/
#
# Setup apt
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add redis && \ 
    cp /root/dist/redis.conf /etc && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /tmp/* /var/tmp/* && \
    rm -rf /var/cache/apk/*
#
# Start redis
STOPSIGNAL SIGKILL
USER nobody:nobody
CMD redis-server /etc/redis.conf
