FROM alpine:edge AS builder

RUN apk add --no-cache \
        autoconf automake autoconf-archive \
        git sqlite-dev cmocka-dev util-linux-dev curl-dev \
        pcre2-dev jansson-dev libmicrohttpd-dev build-base \
        libtool rust rust-bindgen clang22-libclang cargo cmake && \
    apk add --no-cache -X https://dl-cdn.alpinelinux.org/alpine/edge/testing \
        libosip2-dev

RUN git clone --depth 1 --branch v5.0.0 \
    https://github.com/SentryPeer/SentryPeer.git /SentryPeer && \
    test "$(git -C /SentryPeer rev-parse HEAD)" = 96fde68ff916587830e9ff6886c1627a1c29b4a0

WORKDIR /SentryPeer

RUN sed -i '/AM_LDFLAGS=/d' Makefile.am && \
    ./bootstrap.sh && \
    ./configure --disable-opendht && \
    make && \
    make check

FROM alpine:edge

RUN apk add --no-cache \
        jansson libcurl libgcc libmicrohttpd libuuid pcre2 sqlite-libs && \
    apk add --no-cache -X https://dl-cdn.alpinelinux.org/alpine/edge/testing \
        libosip2 && \
    addgroup -g 2000 sentrypeer && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 sentrypeer && \
    mkdir -p /var/log/sentrypeer /home/sentrypeer/.config/sentrypeer && \
    chown -R sentrypeer:sentrypeer /var/log/sentrypeer /home/sentrypeer

COPY --from=builder /SentryPeer/sentrypeer /usr/bin/sentrypeer
COPY --chown=2000:2000 dist/default-config.toml /home/sentrypeer/.config/sentrypeer/

STOPSIGNAL SIGKILL
USER sentrypeer:sentrypeer
WORKDIR /var/log/sentrypeer/
CMD ["/usr/bin/sentrypeer", "-jar", "-f", "/var/log/sentrypeer/sentrypeer.db", "-l", "/var/log/sentrypeer/sentrypeer.json"]
