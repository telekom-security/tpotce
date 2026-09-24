FROM ghcr.io/astral-sh/uv:0.11.21-alpine AS uv-bin

FROM --platform=$BUILDPLATFORM alpine:3.23 AS satori-src

ARG SATORI_COMMIT=73fa88fe6549995c68760be10631382df4ec1d1c
RUN apk add --no-cache \
        ca-certificates \
        git && \
    mkdir -p /opt/satori && \
    cd /opt/satori && \
    git init --quiet && \
    git remote add origin https://github.com/xnih/satori.git && \
    git fetch --quiet --depth 1 origin ${SATORI_COMMIT} && \
    git -c advice.detachedHead=false checkout --quiet FETCH_HEAD && \
    rm -rf .git

FROM alpine:3.23 AS python-deps

ENV UV_NO_CACHE=1

COPY --from=uv-bin /usr/local/bin/uv /usr/local/bin/uv

# Use Alpine's netifaces package; the upstream source does not build cleanly on musl.
RUN apk add --no-cache \
        build-base \
        libpcap-dev \
        linux-headers \
        python3-dev \
        python3 && \
    uv pip install \
        --target /opt/python-deps \
        --python /usr/bin/python3 \
        pcapyplus \
        pypacker \
        requests \
        untangle

FROM alpine:3.23

ARG SATORI_COMMIT=73fa88fe6549995c68760be10631382df4ec1d1c
ENV SATORI_COMMIT=${SATORI_COMMIT} \
    PYTHONPATH=/opt/python-deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

#
# Install packages
RUN apk add --no-cache \
        ca-certificates \
        iproute2 \
        libcap \
        libpcap \
        libstdc++ \
        python3 \
        py3-netifaces \
        py3-setuptools && \
#
# Setup user
    addgroup -g 2000 -S satori && \
    adduser -S -D -H -u 2000 -G satori -h /opt/satori -s /sbin/nologin satori && \
#
# Setup permissions
    mkdir -p /var/log/satori && \
    setcap cap_net_raw,cap_net_admin=+eip $(readlink -f /usr/bin/python3) && \
    chown -R satori:satori /var/log/satori && \
#
# Clean up
    rm -rf /root/.cache \
           /tmp/*

COPY --from=python-deps /opt/python-deps /opt/python-deps
COPY --from=satori-src --chown=2000:2000 /opt/satori /opt/satori
COPY dist/satori_json.py /opt/tpot/satori_json.py

#
# Start Satori
STOPSIGNAL SIGINT
WORKDIR /opt/satori
USER satori:satori
ENTRYPOINT ["python3", "/opt/tpot/satori_json.py"]
CMD ["--modules", "tcp", "--limit", "0", "--log", "/var/log/satori/satori.json"]
