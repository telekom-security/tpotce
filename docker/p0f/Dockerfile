# In case of problems Alpine 3.13 needs to be used:
# https://wiki.alpinelinux.org/wiki/Release_Notes_for_Alpine_3.14.0#faccessat2
FROM alpine:3.17
#
# Add source
COPY . /opt/p0f
#
# Install packages
RUN apk -U --no-cache add \
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
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start p0f
WORKDIR /opt/p0f
USER p0f:p0f
CMD exec /opt/p0f/p0f -u p0f -j -o /var/log/p0f/p0f.json -i $(/sbin/ip address show | /usr/bin/awk '/inet.*brd/{ print $NF; exit }') > /dev/null
