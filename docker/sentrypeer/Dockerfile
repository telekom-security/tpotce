FROM alpine:3.15 as builder
#
RUN apk -U add --no-cache \
            autoconf \
	    automake \
	    autoconf-archive \
	    build-base \
	    curl-dev \
	    cmocka-dev \
	    git \
	    jansson-dev \
	    libmicrohttpd-dev \
            pcre2-dev \
	    sqlite-dev \
	    util-linux-dev
#
RUN apk -U add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing \
            libosip2-dev
#
# Download SentryPeer sources and build
RUN git clone https://github.com/SentryPeer/SentryPeer -b v1.2.0
#
WORKDIR /SentryPeer
#
RUN ./bootstrap.sh
RUN ./configure --disable-opendht --disable-zyre
RUN make
RUN make check
RUN make install
#RUN tar cvfz sp.tgz /SentryPeer/* && \
#    mv sp.tgz /
#
FROM alpine:3.15
#
#COPY --from=builder /sp.tgz /root
COPY --from=builder /SentryPeer/sentrypeer /opt/sentrypeer/
#
# Install packages
RUN apk -U add --no-cache \
            jansson \
            libmicrohttpd \
	    libuuid \
            pcre2 \
	    sqlite-libs && \
    apk -U add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing \
            libosip2 && \
#
# Extract from builder
#    mkdir /opt/sentrypeer && \
#    tar xvfz /root/sp.tgz --strip-components=1 -C /opt/sentrypeer/ && \
#
# Setup user, groups and configs
    mkdir -p /var/log/sentrypeer && \
    addgroup -g 2000 sentrypeer && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 sentrypeer && \
    chown -R sentrypeer:sentrypeer /opt/sentrypeer && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start sentrypeer
STOPSIGNAL SIGKILL
USER sentrypeer:sentrypeer
WORKDIR /opt/sentrypeer/
CMD ./sentrypeer -jar -f /var/log/sentrypeer/sentrypeer.db -l /var/log/sentrypeer/sentrypeer.json
