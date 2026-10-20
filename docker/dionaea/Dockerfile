# syntax=docker/dockerfile:1
# Override the source with --build-context dionaea-src=<path to a checkout of the fork>
# (at DIONAEA_COMMIT) to test a change of the fork before it is tagged.
FROM ubuntu:26.04 AS checkout
ARG DEBIAN_FRONTEND=noninteractive
ARG DIONAEA_COMMIT=9c6213e3a6cdde072e8a77ee4acfb80696d4aff5
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    git clone --depth 1 --branch 0.12.3 https://github.com/t3chn0m4g3/dionaea.git /checkout && \
    test "$(git -C /checkout rev-parse HEAD)" = "${DIONAEA_COMMIT}" && \
    rm -rf /checkout/.git

FROM scratch AS dionaea-src
COPY --from=checkout /checkout/ /

FROM ubuntu:26.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      cmake \
      cython3 \
      libcurl4-openssl-dev \
      libev-dev \
      libglib2.0-dev \
      libnetfilter-queue-dev \
      libpcap-dev \
      libssl-dev \
      libudns-dev \
      pkg-config \
      python3 \
      python3-dev \
      python3-setuptools

COPY --from=dionaea-src / /src

RUN mkdir -p /src/build && \
    cd /src/build && \
    cmake -DCMAKE_INSTALL_PREFIX:PATH=/opt/dionaea -DCMAKE_BUILD_TYPE=RelWithDebInfo -DRELEASE_BUILD=ON .. && \
    make -j"$(nproc)" && \
    make install


FROM ubuntu:26.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      libcap2-bin \
      libcurl4t64 \
      libev4t64 \
      libglib2.0-0t64 \
      libnetfilter-queue1 \
      libpcap0.8t64 \
      libpython3.14 \
      libudns0t64 \
      python3 \
      python3-boto3 \
      python3-bson \
      python3-yaml \
      procps && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/*

COPY --from=builder /opt/dionaea /opt/dionaea

RUN addgroup --gid 2000 dionaea && \
    adduser --system --no-create-home --shell /bin/bash --uid 2000 --disabled-password --disabled-login --gid 2000 dionaea && \
    setcap cap_net_bind_service=+ep /opt/dionaea/sbin/dionaea && \
    chown -R dionaea:dionaea /opt/dionaea/var && \
    rm -rf /opt/dionaea/etc/dionaea/*

COPY dist/etc/ /opt/dionaea/etc/dionaea/
COPY dist/cpu_check.py /cpu_check.py

STOPSIGNAL SIGINT
HEALTHCHECK --interval=5m --timeout=30s --retries=3 CMD python3 /cpu_check.py $(pgrep -of dionaea) 99
USER dionaea:dionaea
CMD ["/opt/dionaea/sbin/dionaea", "-u", "dionaea", "-g", "dionaea", "-c", "/opt/dionaea/etc/dionaea/dionaea.cfg"]
